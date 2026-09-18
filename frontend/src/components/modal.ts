/**
 * 统一模态框：焦点圈定、Escape、焦点恢复和 backdrop 都只在这里实现一次。
 *
 * 领域模块不再自己操作 `show` class、`aria-hidden` 或 `document.body.style.overflow`，
 * 也不再各自注册 Escape 监听——多个模块各注册一份会互相抢关闭动作（阶段 3 的
 * 重复监听风险），而 body overflow 由各自置空会让后开的模态框关掉时提前解锁滚动。
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export interface ModalOptions {
  /** 关闭后把焦点还给谁。返回 null 表示不恢复焦点。 */
  returnFocus?: () => HTMLElement | null;
  /** 点击 backdrop 是否关闭，默认 true。 */
  closeOnBackdrop?: boolean;
  /** Escape 是否关闭，默认 true。 */
  closeOnEscape?: boolean;
  onOpen?: () => void;
  onClose?: () => void;
}

export interface ModalHandle {
  readonly root: HTMLElement;
  open(): void;
  close(): void;
  isOpen(): boolean;
  destroy(): void;
  /**
   * Escape 或点击 backdrop 时的关闭动作。未设置时直接 `close()`。
   *
   * 领域侧的关闭往往还要清理草稿（例如打卡表单的餐盘估算），所以关闭必须能被
   * 接管——但接管者仍然只有一个，不会像 legacy 那样每个模态框各注册一份监听。
   */
  onRequestClose?: () => void;
}

interface Registered {
  handle: ModalHandle;
  options: ModalOptions;
  restore: HTMLElement | null;
}

const stack: Registered[] = [];
let keydownBound = false;

/**
 * 丢掉根节点已经脱离文档的条目。
 *
 * 一个模态框的宿主被整体替换掉（页面重建、领域模块重挂载）时，它不会走 close()，
 * 于是会永远留在栈里——Escape 会打在一个看不见的模态框上，body 的滚动锁也再也
 * 解不开。清理放在每次读栈之前，保证栈顶始终是真正可见的那一个。
 */
function prune(): void {
  for (let index = stack.length - 1; index >= 0; index -= 1) {
    if (!stack[index].handle.root.isConnected) stack.splice(index, 1);
  }
  if (stack.length === 0) document.body.style.overflow = '';
}

function topmost(): Registered | undefined {
  prune();
  return stack[stack.length - 1];
}

/**
 * 可聚焦且当前可见的元素，按 DOM 顺序。
 *
 * 刻意不用 `offsetParent`/`offsetHeight` 判可见：那些在 jsdom 里恒为 null/0，会把
 * 焦点环缩成一个元素，让 Tab 圈定看起来"通过"却什么都没圈住。改用 `hidden` 属性
 * 与计算样式，两种环境下语义一致。
 */
function focusableWithin(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => {
    if (element.closest('[hidden]')) return false;
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
}

function trapTab(event: KeyboardEvent, root: HTMLElement): void {
  const items = focusableWithin(root);
  if (items.length === 0) {
    event.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;

  // 焦点还在框外（刚打开时通常停在触发按钮上，或被浏览器放在 body）：下一次 Tab
  // 必须先进入框内，否则用户会一路 Tab 到底层页面上去。
  if (!root.contains(active)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
    return;
  }
  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

function requestClose(handle: ModalHandle): void {
  if (handle.onRequestClose) handle.onRequestClose();
  else handle.close();
}

function bindGlobalKeydown(): void {
  if (keydownBound) return;
  keydownBound = true;
  document.addEventListener('keydown', (event) => {
    const current = topmost();
    if (!current) return;
    if (event.key === 'Escape' && current.options.closeOnEscape !== false) {
      event.preventDefault();
      requestClose(current.handle);
      return;
    }
    if (event.key === 'Tab') trapTab(event, current.handle.root);
  });
}

export function createModal(root: HTMLElement, options: ModalOptions = {}): ModalHandle {
  bindGlobalKeydown();
  let entry: Registered | null = null;

  const onBackdropClick = (event: MouseEvent): void => {
    if (options.closeOnBackdrop === false) return;
    if (event.target === root) requestClose(handle);
  };

  const handle: ModalHandle = {
    root,
    isOpen: () => entry !== null,
    open() {
      if (entry) return;
      const active = document.activeElement;
      entry = {
        handle,
        options,
        restore: active instanceof HTMLElement ? active : null,
      };
      stack.push(entry);
      root.classList.add('show');
      root.setAttribute('aria-hidden', 'false');
      document.body.style.overflow = 'hidden';
      options.onOpen?.();
    },
    close() {
      if (!entry) return;
      const closing = entry;
      entry = null;
      const index = stack.indexOf(closing);
      if (index >= 0) stack.splice(index, 1);
      prune();
      root.classList.remove('show');
      root.setAttribute('aria-hidden', 'true');
      // 还有别的模态框开着时不能解锁滚动，否则底层页面会在上层还在时开始滚。
      if (stack.length === 0) document.body.style.overflow = '';
      options.onClose?.();
      const target = options.returnFocus ? options.returnFocus() : closing.restore;
      target?.focus();
    },
    destroy() {
      handle.close();
      root.removeEventListener('click', onBackdropClick);
    },
  };

  root.addEventListener('click', onBackdropClick);
  return handle;
}

/** 仅用于测试与开发诊断：当前打开的模态框数量。 */
export function openModalCount(): number {
  prune();
  return stack.length;
}
