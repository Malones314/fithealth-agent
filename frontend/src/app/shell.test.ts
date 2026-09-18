import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createShell, installShell, resetShell, shell } from './shell';

function mountPage(): void {
  document.body.innerHTML = `
    <button id="btn-overview">总览</button>
    <button id="btn-checkin">记录</button>
    <button id="btn-data">数据</button>
    <div id="chat"></div>
    <div class="modal-backdrop" id="health-modal" aria-hidden="true"><div role="dialog"><button>x</button></div></div>
    <div class="modal-backdrop" id="checkin-modal" aria-hidden="true"><div role="dialog"><button>x</button></div></div>
    <div class="modal-backdrop" id="data-modal" aria-hidden="true"><div role="dialog"><button>x</button></div></div>
    <div class="modal-backdrop" id="activity-picker-modal" aria-hidden="true"><div role="dialog"><button>x</button></div></div>
    <div class="trend-periods">
      <button class="trend-period active" data-value="day">日</button>
      <button class="trend-period" data-value="week">周</button>
    </div>`;
}

beforeEach(() => {
  resetShell();
  document.body.style.overflow = '';
  mountPage();
});

describe('app/shell', () => {
  it('refuses to hand out an uninstalled shell instead of silently no-oping', () => {
    expect(() => shell()).toThrow(/not installed/);
  });

  it('returns the same modal handle for a name, so listeners register once', () => {
    const instance = installShell();
    expect(instance.modal('data')).toBe(instance.modal('data'));
  });

  it('opens and closes named modals through the shared modal', () => {
    const instance = installShell();
    instance.openModal('health');
    const root = document.querySelector('#health-modal') as HTMLElement;
    expect(root.classList.contains('show')).toBe(true);
    expect(instance.isModalOpen('health')).toBe(true);
    instance.closeModal('health');
    expect(root.getAttribute('aria-hidden')).toBe('true');
    expect(document.body.style.overflow).toBe('');
  });

  it('returns focus to the button that owns each modal', () => {
    const instance = installShell();
    instance.openModal('data');
    instance.closeModal('data');
    expect(document.activeElement?.id).toBe('btn-data');
  });

  it('reports a missing modal root instead of failing silently', () => {
    document.querySelector('#data-modal')?.remove();
    const instance = installShell();
    expect(() => instance.modal('data')).toThrow(/Modal root not found/);
  });

  it('toast appends into the chat log and scrolls it', () => {
    const instance = installShell();
    instance.toast.success('已保存');
    const chat = document.querySelector('#chat') as HTMLElement;
    expect(chat.children).toHaveLength(1);
    expect(chat.firstElementChild?.className).toContain('notify-saved');
  });

  it('renders a replaceable bot message through the sanitizer', () => {
    const instance = installShell();
    instance.message('bot', '**first**<script>alert(1)</script>', {
      className: 'session-intro',
      replaceClass: 'session-intro',
    });
    instance.message('bot', 'second', {
      className: 'session-intro',
      replaceClass: 'session-intro',
    });
    expect(document.querySelectorAll('.session-intro')).toHaveLength(1);
    expect(document.querySelector('.session-intro')?.textContent).toContain('second');
    expect(document.querySelector('.session-intro script')).toBeNull();
  });

  it('ask joins the question and its consequences', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const instance = installShell();
    expect(instance.ask('删除全部数据吗？', '会先生成恢复点。')).toBe(true);
    expect(confirmSpy).toHaveBeenCalledWith('删除全部数据吗？\n\n会先生成恢复点。');
    confirmSpy.mockRestore();
  });

  it('askRaw keeps the single-argument window.confirm shape for migrated code', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const instance = installShell();
    expect(instance.askRaw('永久删除？')).toBe(false);
    expect(confirmSpy).toHaveBeenCalledWith('永久删除？');
    confirmSpy.mockRestore();
  });

  it('empty renders the shared empty state', () => {
    const instance = installShell();
    const host = document.querySelector('#chat') as HTMLElement;
    instance.empty(host, '暂无记录');
    expect(host.firstElementChild?.className).toBe('data-empty');
  });

  it('registerTabs is idempotent per key, so clicks are not double-bound', () => {
    const instance = installShell();
    const onChange = vi.fn();
    const root = document.querySelector('.trend-periods') as HTMLElement;
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.trend-period'));
    const first = instance.registerTabs('trend-period', root, buttons, { onChange });
    const second = instance.registerTabs('trend-period', root, buttons, { onChange });
    expect(second).toBe(first);
    buttons[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('tabs returns null for an unregistered key so callers can skip safely', () => {
    expect(installShell().tabs('nope')).toBeNull();
  });

  it('installShell accepts an injected double for domain tests', () => {
    const fake = createShell();
    expect(installShell(fake)).toBe(fake);
    expect(shell()).toBe(fake);
  });
});
