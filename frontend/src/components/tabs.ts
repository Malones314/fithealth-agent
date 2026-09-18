/**
 * 单选按钮组（趋势周期 日/周/月、右侧数据类型 训练/营养）。
 *
 * legacy 两处都手写 `forEach(item => item.classList.toggle('active', item === button))`，
 * 且都没有 ARIA 角色——键盘用户只能靠 Tab 逐个走，读屏软件读不出"哪个是当前项"。
 * 这里补上 tablist/tab + aria-selected + 左右方向键，视觉 class 仍是 `active`。
 */

export interface TabsOptions<T extends string> {
  /**
   * 从按钮读出它代表的值；默认读 `data-value`。
   *
   * 刻意**不**叫 `valueOf`：那个名字在 `Object.prototype` 上已经存在，
   * `options.valueOf ?? fallback` 永远拿到继承来的方法，默认实现根本不会生效。
   */
  readValue?: (button: HTMLButtonElement) => T;
  onChange(value: T, button: HTMLButtonElement): void;
  /** 初始选中值；省略则沿用 DOM 里已带 `active` 的那个。 */
  initial?: T;
  ariaLabel?: string;
}

export interface TabsHandle<T extends string> {
  /** 选中并触发 onChange。 */
  select(value: T): void;
  /**
   * 只同步视觉与 ARIA，不触发 onChange。
   *
   * 供"状态已经由别处改掉了，按钮组需要跟上"的情况使用——否则会和调用方形成
   * onChange → 状态变更 → 再 select → onChange 的回环。
   */
  sync(value: T): void;
  current(): T | null;
  destroy(): void;
}

export function createTabs<T extends string>(
  root: HTMLElement,
  buttons: readonly HTMLButtonElement[],
  options: TabsOptions<T>,
): TabsHandle<T> {
  const readValue = options.readValue ?? ((button) => (button.dataset.value ?? '') as T);
  let active: T | null = null;

  root.setAttribute('role', 'tablist');
  if (options.ariaLabel) root.setAttribute('aria-label', options.ariaLabel);
  buttons.forEach((button) => {
    button.setAttribute('role', 'tab');
    button.type = 'button';
  });

  function paint(value: T): void {
    active = value;
    buttons.forEach((button) => {
      const selected = readValue(button) === value;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
      // 方向键在组内移动，Tab 键进出整组：只有当前项留在 tab 序列里。
      button.tabIndex = selected ? 0 : -1;
    });
  }

  function select(value: T, notify = true): void {
    const button = buttons.find((item) => readValue(item) === value);
    if (!button) return;
    paint(value);
    if (notify) options.onChange(value, button);
  }

  const onClick = (event: Event): void => {
    const button = (event.currentTarget as HTMLButtonElement) ?? null;
    if (!button) return;
    select(readValue(button));
  };

  const onKeydown = (event: KeyboardEvent): void => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    const index = buttons.findIndex((button) => readValue(button) === active);
    const next = buttons[(index + step + buttons.length) % buttons.length];
    next.focus();
    select(readValue(next));
  };

  buttons.forEach((button) => {
    button.addEventListener('click', onClick);
    button.addEventListener('keydown', onKeydown);
  });

  // 初始值：显式指定优先，否则沿用 DOM 里已带 active 的那个，再退到第一个。
  // 只 paint 不 notify——启动时不应该触发一次"用户切换了"。
  const preselected = buttons.find((button) => button.classList.contains('active')) ?? buttons[0];
  const initial = options.initial ?? (preselected ? readValue(preselected) : null);
  if (initial !== null) paint(initial);

  return {
    select: (value) => select(value),
    sync: (value) => select(value, false),
    current: () => active,
    destroy() {
      buttons.forEach((button) => {
        button.removeEventListener('click', onClick);
        button.removeEventListener('keydown', onKeydown);
      });
    },
  };
}
