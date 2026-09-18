import { describe, expect, it, vi } from 'vitest';
import { createOperationRegistry, installUnloadCleanup } from './operations';

describe('shared/operations', () => {
  it('hands out increasing tokens per key', () => {
    const registry = createOperationRegistry();
    expect(registry.begin('trend').token).toBe(1);
    expect(registry.begin('trend').token).toBe(2);
  });

  it('keeps token counters independent per key', () => {
    const registry = createOperationRegistry();
    registry.begin('trend');
    registry.begin('trend');
    expect(registry.begin('overview').token).toBe(1);
  });

  it('only the newest round is current, so a slow response cannot repaint', () => {
    const registry = createOperationRegistry();
    const first = registry.begin('trend');
    expect(first.isCurrent()).toBe(true);
    const second = registry.begin('trend');
    // 快速切日期：先发的那一轮回来时必须发现自己已经过期。
    expect(first.isCurrent()).toBe(false);
    expect(second.isCurrent()).toBe(true);
  });

  it('starting a new round aborts the previous one with AbortError', () => {
    const registry = createOperationRegistry();
    const first = registry.begin('trend');
    registry.begin('trend');
    expect(first.signal.aborted).toBe(true);
    expect((first.signal.reason as DOMException).name).toBe('AbortError');
  });

  it('cancel aborts the in-flight round', () => {
    const registry = createOperationRegistry();
    const operation = registry.begin('overview');
    registry.cancel('overview');
    expect(operation.signal.aborted).toBe(true);
    expect(operation.isCurrent()).toBe(false);
  });

  it('cancel on an unknown key is a no-op', () => {
    const registry = createOperationRegistry();
    expect(() => registry.cancel('nope')).not.toThrow();
  });

  it('a round started after cancel does not reuse the old token', () => {
    const registry = createOperationRegistry();
    const first = registry.begin('viewer');
    registry.cancel('viewer');
    const second = registry.begin('viewer');
    // 撞号会让过期的那一轮重新变成"当前"，于是旧响应又能写 UI 了。
    expect(second.token).toBeGreaterThan(first.token);
  });

  it('cancelAll aborts every key', () => {
    const registry = createOperationRegistry();
    const trend = registry.begin('trend');
    const overview = registry.begin('overview');
    registry.cancelAll();
    expect(trend.signal.aborted).toBe(true);
    expect(overview.signal.aborted).toBe(true);
    expect(registry.pending()).toEqual([]);
  });

  it('pending reports only rounds still in flight', () => {
    const registry = createOperationRegistry();
    registry.begin('trend');
    registry.begin('overview');
    registry.cancel('trend');
    expect(registry.pending()).toEqual(['overview']);
  });

  it('unload cleanup cancels everything on pagehide', () => {
    const registry = createOperationRegistry();
    const cancelAll = vi.spyOn(registry, 'cancelAll');
    installUnloadCleanup(registry);
    window.dispatchEvent(new Event('pagehide'));
    expect(cancelAll).toHaveBeenCalled();
  });

  it('unload cleanup also covers beforeunload', () => {
    const registry = createOperationRegistry();
    const cancelAll = vi.spyOn(registry, 'cancelAll');
    installUnloadCleanup(registry);
    window.dispatchEvent(new Event('beforeunload'));
    expect(cancelAll).toHaveBeenCalled();
  });
});
