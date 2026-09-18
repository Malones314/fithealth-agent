import { describe, expect, it, vi } from 'vitest';
import { alwaysConfirm, createConfirmer, neverConfirm } from './confirm-dialog';

describe('components/confirm-dialog', () => {
  it('passes the message straight through when there is no detail', () => {
    const prompt = vi.fn(() => true);
    const confirm = createConfirmer(prompt);
    expect(confirm({ message: '永久删除这条临时记忆吗？' })).toBe(true);
    expect(prompt).toHaveBeenCalledWith('永久删除这条临时记忆吗？');
  });

  it('appends the consequences below the question', () => {
    const prompt = vi.fn(() => true);
    createConfirmer(prompt)({
      message: '删除全部数据吗？',
      detail: '删除前会自动生成一份恢复点。',
    });
    expect(prompt).toHaveBeenCalledWith('删除全部数据吗？\n\n删除前会自动生成一份恢复点。');
  });

  it('a declined prompt blocks the destructive action', () => {
    expect(createConfirmer(() => false)({ message: '删除？' })).toBe(false);
  });

  it('test doubles cover both outcomes', () => {
    expect(alwaysConfirm({ message: 'x' })).toBe(true);
    expect(neverConfirm({ message: 'x' })).toBe(false);
  });
});
