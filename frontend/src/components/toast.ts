/**
 * 统一全局提示。
 *
 * 现有视觉沿用聊天流内的 `.msg.system` 通知条（`notify-saved` / `notify-skipped`
 * / `notify-error`），阶段 4 不夹带视觉改版；这里只把"构造 DOM + 追加 + 滚到底"
 * 这套重复逻辑收敛成一个接口，领域模块不再自己拼 `msg system` 结构。
 */

export type ToastLevel = 'info' | 'success' | 'error';

const LEVEL_CLASS: Record<ToastLevel, string> = {
  success: 'notify-saved',
  info: 'notify-skipped',
  error: 'notify-error',
};

export interface ToastOptions {
  level?: ToastLevel;
  /** 追加一段"已保存摘要"块，纯文本写入，不走 innerHTML。 */
  summary?: string;
}

export interface Toaster {
  show(text: string, options?: ToastOptions): HTMLElement;
  success(text: string, options?: Omit<ToastOptions, 'level'>): HTMLElement;
  info(text: string, options?: Omit<ToastOptions, 'level'>): HTMLElement;
  error(text: string, options?: Omit<ToastOptions, 'level'>): HTMLElement;
}

export function createToaster(host: HTMLElement, onAppend?: () => void): Toaster {
  function show(text: string, options: ToastOptions = {}): HTMLElement {
    const element = document.createElement('div');
    const level = options.level ?? 'info';
    element.className = `msg system ${LEVEL_CLASS[level]}`;
    element.setAttribute('role', level === 'error' ? 'alert' : 'status');

    const body = document.createElement('div');
    body.textContent = text;
    element.append(body);

    if (options.summary) {
      const block = document.createElement('div');
      block.className = 'summary-block';
      const title = document.createElement('strong');
      title.textContent = '已保存摘要';
      const detail = document.createElement('span');
      // textContent 而不是 innerHTML：摘要来自模型输出，不能当富文本写。
      detail.textContent = options.summary;
      block.append(title, detail);
      element.append(block);
    }

    host.append(element);
    onAppend?.();
    return element;
  }

  return {
    show,
    success: (text, options) => show(text, { ...options, level: 'success' }),
    info: (text, options) => show(text, { ...options, level: 'info' }),
    error: (text, options) => show(text, { ...options, level: 'error' }),
  };
}
