/**
 * 应用外壳：共享组件的唯一实例。
 *
 * 阶段 4 把模态框、Toast、空态、二次确认和文件拖拽从各领域收回到 `components/`。
 * 这个模块负责在启动时把它们实例化一次，并暴露给领域模块和迁移期的
 * `runtime` 与领域模块都调用
 * 这里的接口，而不是自己操作 `show` class、`aria-hidden` 和 body overflow）。
 */

import {
  createConfirmer,
  createDropzone,
  createModal,
  createTabs,
  createToaster,
  renderChunkedList,
  renderEmptyState,
  type ChunkedListHandle,
  type ChunkedListOptions,
  type Confirmer,
  type DropzoneHandle,
  type DropzoneOptions,
  type EmptyVariant,
  type ModalHandle,
  type ModalOptions,
  type TabsHandle,
  type TabsOptions,
  type Toaster,
} from '../components';
import { setSanitizedMarkdown } from '../shared/sanitize';

export type ModalName = 'health' | 'checkin' | 'data' | 'activityPicker';

interface ModalSpec {
  root: string;
  returnFocus?: string;
}

const MODAL_SPECS: Record<ModalName, ModalSpec> = {
  health: { root: '#health-modal', returnFocus: '#btn-overview' },
  checkin: { root: '#checkin-modal', returnFocus: '#btn-checkin' },
  data: { root: '#data-modal', returnFocus: '#btn-data' },
  activityPicker: { root: '#activity-picker-modal' },
};

export interface AppShell {
  modal(name: ModalName): ModalHandle;
  openModal(name: ModalName): void;
  closeModal(name: ModalName): void;
  isModalOpen(name: ModalName): boolean;
  readonly toast: Toaster;
  readonly confirm: Confirmer;
  message(
    role: 'user' | 'bot',
    text: string,
    options?: { className?: string; replaceClass?: string },
  ): HTMLElement;
  /** 破坏性操作的确认，返回 true 表示继续。 */
  ask(message: string, detail?: string): boolean;
  /**
   * 与 `window.confirm` 同形的单参入口，供运行时代码使用。
   * 新代码请用 `ask(message, detail)`，它把"操作"和"后果说明"分开表达。
   */
  askRaw(message: string): boolean;
  empty(host: Element, text: string, variant?: EmptyVariant): void;
  dropzone(root: HTMLElement, options: DropzoneOptions): DropzoneHandle;
  /**
   * 按需渲染长列表。未超阈值时行为与一次性渲染完全一致，不多出任何控件，
   * 所以现有调用点可以无条件换过来。
   */
  list<T>(
    host: Element,
    items: readonly T[],
    renderItem: (item: T, index: number) => Node,
    options?: ChunkedListOptions,
  ): ChunkedListHandle;
  /**
   * 注册一个单选按钮组。重复注册同一个 key 返回已有实例，避免 legacy 与新模块
   * 各注册一份 click 监听（阶段 3 的重复监听闸门）。
   */
  registerTabs<T extends string>(
    key: string,
    root: HTMLElement,
    buttons: readonly HTMLButtonElement[],
    options: TabsOptions<T>,
  ): TabsHandle<T>;
  /** 取已注册的按钮组；未注册时返回 null，供尚未初始化的调用点安全跳过。 */
  tabs<T extends string>(key: string): TabsHandle<T> | null;
}

function resolve(selector: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(selector);
}

export function createShell(): AppShell {
  const modals = new Map<ModalName, ModalHandle>();
  const tabGroups = new Map<string, TabsHandle<string>>();
  const chatHost = resolve('#chat');
  const toast = createToaster(chatHost ?? document.body, () => {
    // 追加提示后滚到底，和 legacy 的 scrollBottom 行为一致。
    if (chatHost) chatHost.scrollTop = chatHost.scrollHeight;
  });
  const confirmer = createConfirmer();

  function modal(name: ModalName): ModalHandle {
    const existing = modals.get(name);
    if (existing) return existing;
    const spec = MODAL_SPECS[name];
    const root = resolve(spec.root);
    if (!root) throw new Error(`Modal root not found: ${spec.root}`);
    const options: ModalOptions = spec.returnFocus
      ? { returnFocus: () => resolve(spec.returnFocus as string) }
      : {};
    const handle = createModal(root, options);
    modals.set(name, handle);
    return handle;
  }

  return {
    modal,
    openModal: (name) => modal(name).open(),
    closeModal: (name) => modal(name).close(),
    isModalOpen: (name) => modal(name).isOpen(),
    toast,
    confirm: confirmer,
    message(role, text, options = {}) {
      const host = chatHost ?? document.body;
      if (options.replaceClass) {
        host.querySelectorAll(`.${options.replaceClass}`).forEach((element) => element.remove());
      }
      const element = document.createElement('div');
      element.className = `msg ${role}${options.className ? ` ${options.className}` : ''}`;
      const header = document.createElement('div');
      header.className = 'msg-header';
      const label = document.createElement('div');
      label.className = 'role';
      label.textContent = role === 'user' ? '你' : '助手';
      header.append(label);
      const body = document.createElement('div');
      body.className = 'msg-body';
      if (role === 'bot') setSanitizedMarkdown(body, text);
      else body.textContent = text;
      element.append(header, body);
      host.append(element);
      if (chatHost) chatHost.scrollTop = chatHost.scrollHeight;
      return element;
    },
    ask: (message, detail) => confirmer({ message, detail }),
    askRaw: (message) => confirmer({ message }),
    empty: (host, text, variant) => renderEmptyState(host, text, variant),
    dropzone: (root, options) => createDropzone(root, options),
    list: (host, items, renderItem, options) => renderChunkedList(host, items, renderItem, options),
    // 注册表天生是异质的（每组的取值类型不同），所以内部按 TabsHandle<string>
    // 存放，在取出时还原成调用方声明的 T。类型参数只在调用点有意义。
    registerTabs<T extends string>(
      key: string,
      root: HTMLElement,
      buttons: readonly HTMLButtonElement[],
      options: TabsOptions<T>,
    ): TabsHandle<T> {
      const existing = tabGroups.get(key);
      if (existing) return existing as TabsHandle<T>;
      const handle = createTabs(root, buttons, options);
      tabGroups.set(key, handle as TabsHandle<string>);
      return handle;
    },
    tabs: <T extends string>(key: string): TabsHandle<T> | null =>
      (tabGroups.get(key) as TabsHandle<T> | undefined) ?? null,
  };
}

let current: AppShell | null = null;

/** 启动时安装；runtime 和领域模块都通过 `shell()` 取用同一实例。 */
export function installShell(instance: AppShell = createShell()): AppShell {
  current = instance;
  return instance;
}

export function shell(): AppShell {
  if (!current) throw new Error('App shell is not installed yet');
  return current;
}

export function resetShell(): void {
  current = null;
}
