import type { ApiError, JsonObject } from '../shared/types';
import { isRecord } from '../shared/validation';

export type ResponseType = 'json' | 'text' | 'blob';

export interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: HeadersInit;
  signal?: AbortSignal;
  timeoutMs?: number;
  responseType?: ResponseType;
  clientCorrelationId?: string;
  retryableOperation?: boolean;
}

export interface DownloadResult {
  blob: Blob;
  filename?: string;
  contentType: string;
}

export class HttpApiError extends Error {
  readonly apiError: ApiError;

  constructor(apiError: ApiError) {
    super(apiError.message);
    this.name = 'HttpApiError';
    this.apiError = apiError;
  }

  get status(): number {
    return this.apiError.status;
  }

  get serverCode(): string | undefined {
    return this.apiError.serverCode;
  }

  get clientCorrelationId(): string {
    return this.apiError.clientCorrelationId;
  }

  get retryable(): boolean {
    return this.apiError.retryable;
  }

  get details(): unknown {
    return this.apiError.details;
  }
}

export function createClientCorrelationId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `client-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function extractMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string' && payload.trim()) return payload.trim();
  if (!isRecord(payload)) return fallback;
  const error = payload.error;
  if (typeof error === 'string' && error.trim()) return error.trim();
  if (isRecord(error) && typeof error.message === 'string') return error.message;
  if (typeof payload.message === 'string' && payload.message.trim()) return payload.message;
  return fallback;
}

function extractServerCode(payload: unknown): string | undefined {
  if (!isRecord(payload)) return undefined;
  if (typeof payload.code === 'string') return payload.code;
  if (isRecord(payload.error) && typeof payload.error.code === 'string') return payload.error.code;
  return undefined;
}

function isRetryable(status: number, method: string, explicit = false): boolean {
  if (explicit) return true;
  if (
    status === 408 ||
    status === 425 ||
    status === 429 ||
    status === 500 ||
    status === 502 ||
    status === 503 ||
    status === 504
  ) {
    return ['GET', 'HEAD', 'OPTIONS', 'PUT', 'DELETE'].includes(method);
  }
  return false;
}

function normalizeHeaders(headers: HeadersInit | undefined): Headers {
  return new Headers(headers);
}

async function readPayload(response: Response, responseType: ResponseType): Promise<unknown> {
  if (responseType === 'blob') return response.blob();
  if (responseType === 'text') return response.text();
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isFormDataBody(body: unknown): body is FormData {
  return typeof FormData !== 'undefined' && body instanceof FormData;
}

function prepareBody(body: unknown, headers: Headers): BodyInit | undefined {
  if (body === undefined) return undefined;
  if (
    isFormDataBody(body) ||
    (typeof Blob !== 'undefined' && body instanceof Blob) ||
    typeof body === 'string' ||
    body instanceof URLSearchParams
  )
    return body;
  headers.set('Content-Type', 'application/json');
  return JSON.stringify(body);
}

function createAbortSignal(
  signal: AbortSignal | undefined,
  timeoutMs: number,
): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(
    () => controller.abort(new DOMException('Request timed out', 'TimeoutError')),
    timeoutMs,
  );
  const abort = () =>
    controller.abort(signal?.reason ?? new DOMException('Request cancelled', 'AbortError'));
  if (signal) {
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => {
      globalThis.clearTimeout(timeout);
      signal?.removeEventListener('abort', abort);
    },
  };
}

export function createApiClient(baseUrl = '') {
  async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = (options.method ?? 'GET').toUpperCase();
    const responseType = options.responseType ?? 'json';
    const clientCorrelationId = options.clientCorrelationId ?? createClientCorrelationId();
    const headers = normalizeHeaders(options.headers);
    headers.set(
      'Accept',
      headers.get('Accept') ?? (responseType === 'blob' ? '*/*' : 'application/json'),
    );
    headers.set('X-Client-Correlation-ID', clientCorrelationId);
    const body = prepareBody(options.body, headers);
    const timeoutMs = options.timeoutMs ?? 30_000;
    const abort = createAbortSignal(options.signal, timeoutMs);
    try {
      let response: Response;
      try {
        response = await fetch(`${baseUrl}${path}`, {
          method,
          headers,
          body,
          signal: abort.signal,
        });
      } catch (cause) {
        if (abort.signal.aborted) {
          const reason = abort.signal.reason;
          if (reason?.name === 'TimeoutError') {
            throw new HttpApiError({
              status: 0,
              message: `请求超时（${timeoutMs}ms）`,
              clientCorrelationId,
              endpoint: path,
              retryable: true,
              details: cause,
            });
          }
          throw reason instanceof Error
            ? reason
            : new DOMException('Request cancelled', 'AbortError');
        }
        throw new HttpApiError({
          status: 0,
          message: '网络连接失败',
          clientCorrelationId,
          endpoint: path,
          retryable: true,
          details: cause,
        });
      }
      const payload = await readPayload(response, responseType);
      if (!response.ok) {
        const apiError: ApiError = {
          status: response.status,
          message: extractMessage(payload, `请求失败（HTTP ${response.status}）`),
          serverCode: extractServerCode(payload),
          clientCorrelationId,
          endpoint: path,
          retryable: isRetryable(response.status, method, options.retryableOperation),
          details: payload,
        };
        throw new HttpApiError(apiError);
      }
      return payload as T;
    } finally {
      abort.cleanup();
    }
  }

  async function download(
    path: string,
    options: Omit<RequestOptions, 'responseType'> = {},
  ): Promise<DownloadResult> {
    const method = (options.method ?? 'GET').toUpperCase();
    const clientCorrelationId = options.clientCorrelationId ?? createClientCorrelationId();
    const headers = normalizeHeaders(options.headers);
    headers.set('Accept', '*/*');
    headers.set('X-Client-Correlation-ID', clientCorrelationId);
    const abort = createAbortSignal(options.signal, options.timeoutMs ?? 30_000);
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: prepareBody(options.body, headers),
        signal: abort.signal,
      });
    } catch (cause) {
      abort.cleanup();
      if (abort.signal.aborted) {
        const reason = abort.signal.reason;
        if (reason?.name === 'TimeoutError') {
          throw new HttpApiError({
            status: 0,
            message: `请求超时（${options.timeoutMs ?? 30_000}ms）`,
            clientCorrelationId,
            endpoint: path,
            retryable: true,
            details: cause,
          });
        }
        throw reason instanceof Error
          ? reason
          : new DOMException('Request cancelled', 'AbortError');
      }
      throw new HttpApiError({
        status: 0,
        message: '网络连接失败',
        clientCorrelationId,
        retryable: true,
        details: cause,
      });
    }
    if (!response.ok) {
      const payload = await readPayload(response, 'json');
      abort.cleanup();
      throw new HttpApiError({
        status: response.status,
        message: extractMessage(payload, `请求失败（HTTP ${response.status}）`),
        serverCode: extractServerCode(payload),
        clientCorrelationId,
        endpoint: path,
        retryable: isRetryable(response.status, method, options.retryableOperation),
        details: payload,
      });
    }
    const result = await response.blob();
    const contentDisposition = response.headers.get('Content-Disposition') ?? '';
    const match = contentDisposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)/i);
    abort.cleanup();
    return {
      blob: result,
      filename: match?.[1] ? decodeURIComponent(match[1]) : undefined,
      contentType: response.headers.get('Content-Type') ?? 'application/octet-stream',
    };
  }

  return { request, download };
}

export const apiClient = createApiClient();

export function asJsonObject(value: unknown): JsonObject {
  return isRecord(value) ? value : {};
}
