/**
 * 未处理异常捕获。
 *
 * 计划的约束很具体：**只**记录模块、endpoint、状态码和 clientCorrelationId。
 * 健康数据、密钥和完整聊天正文不得进入前端日志，所以这里刻意不记录：
 *   - 异常 message（可能带回服务器返回的记录内容或用户输入）；
 *   - stack（含源码片段，且对诊断这类问题没有额外价值）；
 *   - 任何请求/响应 body。
 *
 * 取消（AbortError）不是异常：切日期、关弹窗、离开编辑模式都会主动取消在飞的
 * 请求，把它们报成业务失败会让控制台充满假错误，也会让阶段 5 的"控制台无未处理
 * 异常"门槛变得没有意义。
 */

import { HttpApiError } from '../api/client';
import { isAbort } from '../shared/operations';
export { isAbort } from '../shared/operations';

export interface ErrorReport {
  /** 出错的模块，例如 `session`、`workout`。未知时为 `unknown`。 */
  module: string;
  /** 请求路径，已剥掉查询串（查询串可能含日期等可推断信息）。 */
  endpoint?: string;
  status?: number;
  serverCode?: string;
  clientCorrelationId?: string;
  /** 异常构造函数名，不含 message。 */
  kind: string;
}

export type ErrorSink = (report: ErrorReport) => void;

/** 剥掉查询串与 hash，只留路径。 */
export function scrubEndpoint(url: string | undefined): string | undefined {
  if (!url) return undefined;
  const [withoutHash] = url.split('#');
  const [path] = withoutHash.split('?');
  return path || undefined;
}

export function describe(reason: unknown, module = 'unknown'): ErrorReport | null {
  if (isAbort(reason)) return null;
  if (reason instanceof HttpApiError) {
    return {
      module,
      endpoint: scrubEndpoint(reason.apiError.endpoint),
      status: reason.status,
      serverCode: reason.serverCode,
      clientCorrelationId: reason.clientCorrelationId,
      kind: 'HttpApiError',
    };
  }
  return {
    module,
    kind: reason instanceof Error ? reason.name || 'Error' : typeof reason,
  };
}

function format(report: ErrorReport): string {
  const parts = [`module=${report.module}`, `kind=${report.kind}`];
  if (report.endpoint) parts.push(`endpoint=${report.endpoint}`);
  if (report.status !== undefined) parts.push(`status=${report.status}`);
  if (report.serverCode) parts.push(`serverCode=${report.serverCode}`);
  if (report.clientCorrelationId) parts.push(`correlationId=${report.clientCorrelationId}`);
  return `[frontend] ${parts.join(' ')}`;
}

const defaultSink: ErrorSink = (report) => {
  // 只写控制台，不外发：任何上报端点都会把诊断信息送出本机，违反最小外发要求。
  console.error(format(report));
};

let sink: ErrorSink = defaultSink;
let installed = false;

export function report(reason: unknown, module?: string): void {
  const described = describe(reason, module);
  if (described) sink(described);
}

/**
 * 装上 window 级捕获。重复调用是安全的——不会注册两份监听。
 */
export function installErrorReporting(customSink: ErrorSink = defaultSink): void {
  sink = customSink;
  if (installed) return;
  installed = true;

  window.addEventListener('unhandledrejection', (event) => {
    if (isAbort(event.reason)) {
      // 取消掉的请求不是失败：吞掉它，避免控制台出现假的未处理拒绝。
      event.preventDefault();
      return;
    }
    report(event.reason);
  });

  window.addEventListener('error', (event) => {
    report(event.error ?? new Error(event.type));
  });
}

/** 测试用：还原到未安装状态。 */
export function resetErrorReporting(): void {
  sink = defaultSink;
  installed = false;
}
