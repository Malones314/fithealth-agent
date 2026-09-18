import { afterEach, describe, expect, it, vi } from 'vitest';
import { chatApi, DEFAULT_CHAT_TIMEOUT_MS } from './chat';

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function delayedReply(delayMs: number) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(
    (_input, init) =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => resolve(new Response('{"reply":"plan","artifact":{"type":"training_plan"}}')),
          delayMs,
        );
        init?.signal?.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(init.signal?.reason);
        });
      }),
  );
}

describe('chat request timeout', () => {
  it('receives a generated plan even when the turn takes more than 30 seconds', async () => {
    vi.useFakeTimers();
    const fetchMock = delayedReply(45_000);
    const result = chatApi.send({ message: '生成今日的训练计划' });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(15_000);
    await expect(result).resolves.toMatchObject({ artifact: { type: 'training_plan' } });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('retains explicit cancellation during a long-running turn', async () => {
    vi.useFakeTimers();
    delayedReply(DEFAULT_CHAT_TIMEOUT_MS);
    const controller = new AbortController();
    const result = chatApi.send({ message: '生成计划' }, { signal: controller.signal });
    const assertion = expect(result).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(31_000);
    controller.abort();
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('honors an explicitly supplied timeout without retrying the chat turn', async () => {
    vi.useFakeTimers();
    const fetchMock = delayedReply(45_000);
    const result = chatApi.send({ message: '生成计划' }, { timeoutMs: 1_000 });
    const assertion = expect(result).rejects.toThrow('请求超时');
    await vi.advanceTimersByTimeAsync(1_000);
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
