import { beforeEach, describe, expect, it, vi } from 'vitest';
import { setDisabled, setLoading, withLoading } from './button';

let button: HTMLButtonElement;

beforeEach(() => {
  document.body.innerHTML = '<button id="act">导出备份</button>';
  button = document.querySelector<HTMLButtonElement>('#act') as HTMLButtonElement;
});

describe('components/button', () => {
  it('marks aria-busy and disables while loading', () => {
    setLoading(button, true, { loadingLabel: '导出中…' });
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.textContent).toBe('导出中…');
  });

  it('restores the original label exactly', () => {
    setLoading(button, true, { loadingLabel: '导出中…' });
    setLoading(button, false);
    expect(button.textContent).toBe('导出备份');
    expect(button.disabled).toBe(false);
    expect(button.hasAttribute('aria-busy')).toBe(false);
  });

  it('keeps the first remembered label when loading is entered twice', () => {
    setLoading(button, true, { loadingLabel: '第一次…' });
    setLoading(button, true, { loadingLabel: '第二次…' });
    setLoading(button, false);
    expect(button.textContent).toBe('导出备份');
  });

  it('leaves the label alone when no loadingLabel is given', () => {
    setLoading(button, true);
    expect(button.textContent).toBe('导出备份');
    expect(button.getAttribute('aria-busy')).toBe('true');
  });

  it('setDisabled records a reason as the tooltip', () => {
    setDisabled(button, true, '请先选择一条训练记录');
    expect(button.disabled).toBe(true);
    expect(button.title).toBe('请先选择一条训练记录');
  });

  it('withLoading restores state after a rejection', async () => {
    await expect(
      withLoading(button, () => Promise.reject(new Error('网络异常')), { loadingLabel: '导出中…' }),
    ).rejects.toThrow('网络异常');
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe('导出备份');
    expect(button.hasAttribute('aria-busy')).toBe(false);
  });

  it('withLoading returns the action result and disables during the call', async () => {
    const observed: boolean[] = [];
    const action = vi.fn(async () => {
      observed.push(button.disabled);
      return 'ok';
    });
    await expect(withLoading(button, action)).resolves.toBe('ok');
    expect(observed).toEqual([true]);
    expect(button.disabled).toBe(false);
  });
});
