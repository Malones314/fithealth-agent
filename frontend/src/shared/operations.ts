/**
 * 按操作名管理"在飞请求"的取消与顺序。
 *
 * 解决两类竞态，它们在冻结基线里都是真实存在的：
 *
 * 1. **旧响应覆盖新状态**。快速切日期时会连发多个 `/health/trend`，先发的可能后到，
 *    于是屏幕上留下的是**旧**日期的数据。只比较"有没有在飞"不够——必须给每次操作
 *    发一个递增 token，只有最新 token 允许写 UI。
 * 2. **离开后仍在写**。关弹窗、离开编辑模式、切日期或卸载页面之后，之前那个请求
 *    还会回来渲染一次。所以每个操作名同时持有一个 AbortController，新一轮开始或
 *    显式取消时把上一轮掐掉。
 *
 * 取消走 AbortError 语义，由 `app/error-reporting` 识别为"不是失败"，不会展示成
 * 业务错误，也不会污染控制台。
 */

export interface Operation {
  /** 递增序号；只有等于当前值才允许写 UI。 */
  readonly token: number;
  readonly signal: AbortSignal;
  /** 这一轮是否仍是该操作名下最新的一轮。 */
  isCurrent(): boolean;
}

/** Abort/cancellation is control flow, not a user-visible failure. */
export function isAbort(reason: unknown): boolean {
  if (reason instanceof DOMException) return reason.name === 'AbortError';
  return reason instanceof Error && reason.name === 'AbortError';
}

export interface OperationRegistry {
  /** 开启新一轮：取消上一轮，返回新 token 与 signal。 */
  begin(key: string): Operation;
  /** 取消某个操作名当前在飞的那一轮。 */
  cancel(key: string): void;
  /** 取消全部（页面卸载、会话结束）。 */
  cancelAll(): void;
  /** 诊断用：当前在飞的操作名。 */
  pending(): string[];
}

interface Entry {
  token: number;
  controller: AbortController;
}

export function createOperationRegistry(): OperationRegistry {
  const entries = new Map<string, Entry>();

  function abort(entry: Entry): void {
    // 只有还没结束的才需要 abort；重复 abort 无副作用，但保持语义清晰。
    if (!entry.controller.signal.aborted) {
      entry.controller.abort(new DOMException('Superseded by a newer operation', 'AbortError'));
    }
  }

  return {
    begin(key) {
      const previous = entries.get(key);
      if (previous) abort(previous);
      const token = (previous?.token ?? 0) + 1;
      const controller = new AbortController();
      entries.set(key, { token, controller });
      return {
        token,
        signal: controller.signal,
        isCurrent: () => entries.get(key)?.token === token && !controller.signal.aborted,
      };
    },
    cancel(key) {
      const entry = entries.get(key);
      if (!entry) return;
      abort(entry);
      // 保留 token 计数，避免删除后重新从 1 开始、与仍在飞的旧轮次撞号。
      entries.set(key, { token: entry.token, controller: entry.controller });
    },
    cancelAll() {
      entries.forEach(abort);
    },
    pending() {
      return [...entries.entries()]
        .filter(([, entry]) => !entry.controller.signal.aborted)
        .map(([key]) => key);
    },
  };
}

/** 应用级单例：领域模块与 runtime 共用同一份，取消才能互相看见。 */
export const operations = createOperationRegistry();

/**
 * 页面卸载时取消所有在飞请求。
 *
 * `pagehide` 而不是只用 `beforeunload`：移动端 Safari 走进后台时不触发
 * `beforeunload`，请求会挂在那里直到超时。
 */
export function installUnloadCleanup(registry: OperationRegistry = operations): void {
  const cancel = (): void => registry.cancelAll();
  window.addEventListener('pagehide', cancel);
  window.addEventListener('beforeunload', cancel);
}
