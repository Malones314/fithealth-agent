import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createModal, openModalCount } from './modal';

function mount(id = 'test-modal'): HTMLElement {
  document.body.innerHTML = `
    <button id="opener">打开</button>
    <div class="modal-backdrop" id="${id}" aria-hidden="true">
      <div role="dialog" aria-modal="true">
        <button id="first">首个</button>
        <input id="middle" />
        <button id="last">末个</button>
      </div>
    </div>`;
  return document.querySelector<HTMLElement>(`#${id}`) as HTMLElement;
}

function press(key: string, shiftKey = false): void {
  document.dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey, bubbles: true }));
}

describe('components/modal', () => {
  beforeEach(() => {
    document.body.style.overflow = '';
  });

  it('opening sets show, aria-hidden and locks body scrolling', () => {
    const root = mount();
    const modal = createModal(root);
    modal.open();
    expect(root.classList.contains('show')).toBe(true);
    expect(root.getAttribute('aria-hidden')).toBe('false');
    expect(document.body.style.overflow).toBe('hidden');
    expect(modal.isOpen()).toBe(true);
  });

  it('closing restores aria-hidden and unlocks scrolling', () => {
    const modal = createModal(mount());
    modal.open();
    modal.close();
    expect(modal.root.classList.contains('show')).toBe(false);
    expect(modal.root.getAttribute('aria-hidden')).toBe('true');
    expect(document.body.style.overflow).toBe('');
  });

  it('Escape closes the topmost modal only', () => {
    const first = createModal(mount('modal-a'));
    const firstRoot = document.body.innerHTML;
    expect(firstRoot).toContain('modal-a');
    first.open();
    const secondRoot = document.createElement('div');
    secondRoot.id = 'modal-b';
    document.body.append(secondRoot);
    const second = createModal(secondRoot);
    second.open();

    press('Escape');
    expect(second.isOpen()).toBe(false);
    expect(first.isOpen()).toBe(true);
    // 上层关掉时下层还开着，滚动不能提前解锁。
    expect(document.body.style.overflow).toBe('hidden');

    press('Escape');
    expect(first.isOpen()).toBe(false);
    expect(document.body.style.overflow).toBe('');
  });

  it('onRequestClose takes over Escape and backdrop instead of closing directly', () => {
    const root = mount();
    const modal = createModal(root);
    const handler = vi.fn();
    modal.onRequestClose = handler;
    modal.open();
    press('Escape');
    expect(handler).toHaveBeenCalledTimes(1);
    // 接管者负责决定是否真的关闭。
    expect(modal.isOpen()).toBe(true);
  });

  it('clicking the backdrop closes, clicking inside does not', () => {
    const root = mount();
    const modal = createModal(root);
    modal.open();
    root
      .querySelector('[role="dialog"]')
      ?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(modal.isOpen()).toBe(true);
    root.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(modal.isOpen()).toBe(false);
  });

  it('closeOnBackdrop and closeOnEscape can be disabled', () => {
    const root = mount();
    const modal = createModal(root, { closeOnBackdrop: false, closeOnEscape: false });
    modal.open();
    root.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    press('Escape');
    expect(modal.isOpen()).toBe(true);
  });

  it('restores focus to the trigger on close', () => {
    const root = mount();
    const opener = document.querySelector<HTMLButtonElement>('#opener') as HTMLButtonElement;
    opener.focus();
    const modal = createModal(root);
    modal.open();
    document.querySelector<HTMLElement>('#first')?.focus();
    modal.close();
    expect(document.activeElement).toBe(opener);
  });

  it('returnFocus overrides the remembered trigger', () => {
    const root = mount();
    const opener = document.querySelector<HTMLButtonElement>('#opener') as HTMLButtonElement;
    const modal = createModal(root, { returnFocus: () => opener });
    modal.open();
    modal.close();
    expect(document.activeElement).toBe(opener);
  });

  it('Tab from the last focusable wraps to the first', () => {
    const root = mount();
    const modal = createModal(root);
    modal.open();
    document.querySelector<HTMLElement>('#last')?.focus();
    press('Tab');
    expect(document.activeElement?.id).toBe('first');
  });

  it('Shift+Tab from the first focusable wraps to the last', () => {
    const root = mount();
    const modal = createModal(root);
    modal.open();
    document.querySelector<HTMLElement>('#first')?.focus();
    press('Tab', true);
    expect(document.activeElement?.id).toBe('last');
  });

  it('open and close are idempotent, so the stack cannot leak', () => {
    const modal = createModal(mount());
    modal.open();
    modal.open();
    expect(openModalCount()).toBe(1);
    modal.close();
    modal.close();
    expect(openModalCount()).toBe(0);
  });

  it('onOpen and onClose fire once per transition', () => {
    const onOpen = vi.fn();
    const onClose = vi.fn();
    const modal = createModal(mount(), { onOpen, onClose });
    modal.open();
    modal.open();
    modal.close();
    modal.close();
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
