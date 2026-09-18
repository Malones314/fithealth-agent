/**
 * Button / IconButton 的状态语义。
 *
 * 阶段 4 的目标不是替换现有 `<button>` 标签（那会改动 DOM 结构和视觉），而是把
 * "禁用 + aria-busy + 加载文案还原"这套散落在各领域里的写法收敛成一个接口。
 * 关键行为：进入 loading 前记住原文案，退出时精确还原——legacy 里有多处忘记
 * 还原、把"导出中…"永久留在按钮上的写法。
 */

const ORIGINAL_LABEL = Symbol('originalLabel');

interface Stateful extends HTMLButtonElement {
  [ORIGINAL_LABEL]?: string;
}

export interface ButtonStateOptions {
  /** loading 期间显示的文案；省略则保留原文案，只加 aria-busy。 */
  loadingLabel?: string;
}

export function setLoading(
  button: HTMLButtonElement,
  loading: boolean,
  options: ButtonStateOptions = {},
): void {
  const stateful = button as Stateful;
  if (loading) {
    if (stateful[ORIGINAL_LABEL] === undefined) stateful[ORIGINAL_LABEL] = button.textContent ?? '';
    if (options.loadingLabel !== undefined) button.textContent = options.loadingLabel;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    return;
  }
  const original = stateful[ORIGINAL_LABEL];
  if (original !== undefined) {
    button.textContent = original;
    delete stateful[ORIGINAL_LABEL];
  }
  button.disabled = false;
  button.removeAttribute('aria-busy');
}

export function setDisabled(button: HTMLButtonElement, disabled: boolean, reason = ''): void {
  button.disabled = disabled;
  if (disabled && reason) button.title = reason;
}

/** 把一次异步操作包成"按下即 loading、结束必还原"的形式。 */
export async function withLoading<T>(
  button: HTMLButtonElement,
  action: () => Promise<T>,
  options: ButtonStateOptions = {},
): Promise<T> {
  setLoading(button, true, options);
  try {
    return await action();
  } finally {
    setLoading(button, false);
  }
}
