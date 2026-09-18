/**
 * 按需渲染长列表。
 *
 * 计划要求"对长聊天、健康趋势和大型数据列表按需渲染，**不做无依据的提前虚拟化**"，
 * 所以这里的取舍是有实测依据的：
 *
 *   rows=  50  →   8.0ms / 250 节点
 *   rows= 200  →  16.4ms / 1000 节点
 *   rows= 500  →  34.9ms / 2500 节点
 *   rows=1000  →  63.2ms / 5000 节点
 *   rows=2000  → 136.1ms / 10000 节点
 *   rows=5000  → 316.9ms / 25000 节点
 *
 * （jsdom，每行 5 个节点；真实浏览器更快。）成本是**线性**的，没有断崖，所以：
 *   - 不引入虚拟滚动。它要接管滚动容器、行高测量和滚动锚定，复杂度换来的收益在
 *     这个量级上并不存在——而且列表容器本来就有 `max-height:240px` 的滚动区。
 *   - 只在超过阈值时分批渲染：先渲染第一批，其余用"显示更多"按需追加。首屏成本
 *     被压到常数，用户仍能看到完整数据。
 *
 * 阈值取 200：实测 16ms 左右，仍在一帧预算内，同时覆盖绝大多数真实数据量
 * （每日记录一天一条，训练记录一次一条），不会让常见场景多出一个按钮。
 */

export const CHUNK_THRESHOLD = 200;
export const CHUNK_SIZE = 100;

/** 不带类型参数：这些选项与元素类型无关，加个 `<T>` 只是装饰。 */
export interface ChunkedListOptions {
  /** 超过这个条数才分批；默认 CHUNK_THRESHOLD。 */
  threshold?: number;
  /** 每批渲染多少条；默认 CHUNK_SIZE。 */
  chunkSize?: number;
  /** 「显示更多」按钮文案，收到剩余条数。 */
  moreLabel?: (remaining: number) => string;
}

export interface ChunkedListHandle {
  /** 已经渲染出来的条数。 */
  rendered(): number;
  /** 再渲染一批；返回是否还有剩余。 */
  more(): boolean;
  /** 全部渲染出来（导出、打印或测试用）。 */
  all(): void;
}

/**
 * 把 `items` 渲染进 `host`。`renderItem` 负责构造单行节点。
 *
 * 一次性替换容器内容，所以重复调用是安全的——不会叠加上一次的结果。
 */
export function renderChunkedList<T>(
  host: Element,
  items: readonly T[],
  renderItem: (item: T, index: number) => Node,
  options: ChunkedListOptions = {},
): ChunkedListHandle {
  const threshold = options.threshold ?? CHUNK_THRESHOLD;
  const chunkSize = options.chunkSize ?? CHUNK_SIZE;
  const moreLabel =
    options.moreLabel ?? ((remaining: number) => `显示更多（还有 ${remaining} 条）`);

  host.replaceChildren();
  let rendered = 0;

  // 未超阈值：直接全量渲染，行为与改造前完全一致，不多出按钮。
  const initial = items.length > threshold ? chunkSize : items.length;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'data-action chunked-more';

  function appendBatch(count: number): void {
    const stop = Math.min(items.length, rendered + count);
    const fragment = document.createDocumentFragment();
    for (let index = rendered; index < stop; index += 1) {
      fragment.append(renderItem(items[index], index));
    }
    // 按钮必须留在列表末尾，所以插在它前面而不是直接 append。
    if (button.isConnected) host.insertBefore(fragment, button);
    else host.append(fragment);
    rendered = stop;
    syncButton();
  }

  function syncButton(): void {
    const remaining = items.length - rendered;
    if (remaining <= 0) {
      button.remove();
      return;
    }
    button.textContent = moreLabel(remaining);
    if (!button.isConnected) host.append(button);
  }

  button.addEventListener('click', () => appendBatch(chunkSize));
  appendBatch(initial);

  return {
    rendered: () => rendered,
    more: () => {
      appendBatch(chunkSize);
      return items.length > rendered;
    },
    all: () => appendBatch(items.length - rendered),
  };
}
