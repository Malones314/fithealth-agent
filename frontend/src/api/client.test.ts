import { beforeEach, describe, expect, it, vi } from 'vitest';
import { HttpApiError, createApiClient } from './client';

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('serializes JSON and adds a client correlation id', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    const client = createApiClient('http://api.test');
    await expect(
      client.request('/chat', { method: 'POST', body: { message: 'hello' } }),
    ).resolves.toEqual({ ok: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.headers).toBeInstanceOf(Headers);
    expect((init?.headers as Headers).get('Content-Type')).toBe('application/json');
    expect((init?.headers as Headers).get('X-Client-Correlation-ID')).toBeTruthy();
    expect(init?.body).toBe(JSON.stringify({ message: 'hello' }));
  });

  it('supports FormData without overriding its content type', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));
    const body = new FormData();
    body.append('file', new File(['x'], 'x.fit'));
    await createApiClient().request('/upload_fit', { method: 'POST', body });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.body).toBe(body);
    expect((init?.headers as Headers).get('Content-Type')).toBeNull();
  });

  it.each([
    [403, '外部模型已关闭'],
    [409, '冲突'],
    [422, '字段错误'],
    [500, '服务器错误'],
    [503, '维护中'],
  ])('normalizes HTTP %i errors', async (status, message) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'BUSINESS_ERROR', message } }), { status }),
    );
    await expect(createApiClient().request('/x')).rejects.toSatisfy((error: unknown) => {
      if (!(error instanceof HttpApiError)) return false;
      expect(error.apiError.status).toBe(status);
      expect(error.apiError.message).toBe(message);
      expect(error.apiError.serverCode).toBe('BUSINESS_ERROR');
      expect(error.apiError.clientCorrelationId).toBeTruthy();
      expect(error.apiError.retryable).toBe(status === 500 || status === 503);
      return true;
    });
  });

  it('handles non-JSON error bodies', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html>bad gateway</html>', { status: 502 }),
    );
    await expect(createApiClient().request('/x')).rejects.toMatchObject({
      apiError: { status: 502, message: '<html>bad gateway</html>', retryable: true },
    });
  });

  it('supports cancellation and timeout', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
        }),
    );
    const controller = new AbortController();
    const cancelled = createApiClient().request('/slow', { signal: controller.signal });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
    await expect(createApiClient().request('/slow', { timeoutMs: 1 })).rejects.toThrow('请求超时');
  });

  it('handles network errors and downloads once', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('offline'));
    await expect(createApiClient().request('/offline')).rejects.toThrow('网络连接失败');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fetchMock.mockResolvedValue(
      new Response('zip', {
        status: 200,
        headers: {
          'Content-Disposition': "attachment; filename*=UTF-8''backup.zip",
          'Content-Type': 'application/zip',
        },
      }),
    );
    const downloaded = await createApiClient().download('/backup');
    expect(downloaded.filename).toBe('backup.zip');
    expect(downloaded.contentType).toBe('application/zip');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
