import { beforeEach, describe, expect, it } from 'vitest';
import { createEmptyState, renderEmptyState, renderLoadingState } from './empty-state';

let host: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = '<div id="list"><span>旧内容</span></div>';
  host = document.querySelector<HTMLElement>('#list') as HTMLElement;
});

describe('components/empty-state', () => {
  it('keeps the frozen class per variant', () => {
    expect(createEmptyState('暂无记录').className).toBe('data-empty');
    expect(createEmptyState('暂无训练组', 'workout').className).toBe('workout-empty');
  });

  it('replaces the whole container', () => {
    renderEmptyState(host, '暂无已保存训练记录');
    expect(host.children).toHaveLength(1);
    expect(host.textContent).toBe('暂无已保存训练记录');
  });

  it('writes server-provided text as text, not markup', () => {
    renderEmptyState(host, '<img src=x onerror=alert(1)>读取失败');
    expect(host.querySelector('img')).toBeNull();
    expect(host.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('loading state is announced and marked busy', () => {
    renderLoadingState(host, '正在加载健康数据…');
    const element = host.firstElementChild as HTMLElement;
    expect(element.getAttribute('aria-busy')).toBe('true');
    expect(element.getAttribute('role')).toBe('status');
    expect(element.textContent).toBe('正在加载健康数据…');
  });

  it('loading state replaces a previous empty state', () => {
    renderEmptyState(host, '暂无数据');
    renderLoadingState(host);
    expect(host.children).toHaveLength(1);
    expect(host.textContent).toBe('正在加载…');
  });
});
