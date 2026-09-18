/**
 * 空态与加载态。
 *
 * legacy 里有 13 处 `list.innerHTML = '<div class="data-empty">…</div>'`，其中一处
 * 还把服务器错误文本拼进 HTML（靠手写 escapeHtml 兜底）。这里统一成 DOM 构造，
 * 顺带取消那条 innerHTML 写入路径——空态文本永远是 textContent。
 */

export type EmptyVariant = 'data' | 'workout';

const VARIANT_CLASS: Record<EmptyVariant, string> = {
  data: 'data-empty',
  workout: 'workout-empty',
};

export function createEmptyState(text: string, variant: EmptyVariant = 'data'): HTMLElement {
  const element = document.createElement('div');
  element.className = VARIANT_CLASS[variant];
  element.textContent = text;
  return element;
}

/** 用空态整体替换容器内容。 */
export function renderEmptyState(
  host: Element,
  text: string,
  variant: EmptyVariant = 'data',
): void {
  host.replaceChildren(createEmptyState(text, variant));
}

export function renderLoadingState(host: Element, text = '正在加载…'): void {
  const element = createEmptyState(text, 'data');
  element.setAttribute('aria-busy', 'true');
  element.setAttribute('role', 'status');
  host.replaceChildren(element);
}
