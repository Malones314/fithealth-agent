import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createTabs } from './tabs';

let root: HTMLElement;
let buttons: HTMLButtonElement[];

beforeEach(() => {
  document.body.innerHTML = `
    <div class="trend-periods">
      <button class="trend-period active" data-value="day">日</button>
      <button class="trend-period" data-value="week">周</button>
      <button class="trend-period" data-value="month">月</button>
    </div>`;
  root = document.querySelector<HTMLElement>('.trend-periods') as HTMLElement;
  buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.trend-period'));
});

describe('components/tabs', () => {
  it('applies tablist/tab roles and the accessible name', () => {
    createTabs(root, buttons, { onChange: vi.fn(), ariaLabel: '选择时间范围' });
    expect(root.getAttribute('role')).toBe('tablist');
    expect(root.getAttribute('aria-label')).toBe('选择时间范围');
    expect(buttons.every((button) => button.getAttribute('role') === 'tab')).toBe(true);
  });

  it('adopts the pre-selected active button without firing onChange', () => {
    const onChange = vi.fn();
    const tabs = createTabs(root, buttons, { onChange });
    expect(tabs.current()).toBe('day');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('keeps the legacy active class as the visual state', () => {
    const tabs = createTabs(root, buttons, { onChange: vi.fn() });
    tabs.select('week');
    expect(buttons[1].classList.contains('active')).toBe(true);
    expect(buttons[0].classList.contains('active')).toBe(false);
  });

  it('mirrors selection into aria-selected and the tab sequence', () => {
    const tabs = createTabs(root, buttons, { onChange: vi.fn() });
    tabs.select('month');
    expect(buttons.map((button) => button.getAttribute('aria-selected'))).toEqual([
      'false',
      'false',
      'true',
    ]);
    expect(buttons.map((button) => button.tabIndex)).toEqual([-1, -1, 0]);
  });

  it('clicking notifies onChange with the value and button', () => {
    const onChange = vi.fn();
    createTabs(root, buttons, { onChange });
    buttons[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith('week', buttons[1]);
  });

  it('ArrowRight moves focus and selection, wrapping at the end', () => {
    const onChange = vi.fn();
    const tabs = createTabs(root, buttons, { onChange });
    buttons[2].focus();
    tabs.select('month');
    onChange.mockClear();
    buttons[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(document.activeElement).toBe(buttons[0]);
    expect(onChange).toHaveBeenCalledWith('day', buttons[0]);
  });

  it('ArrowLeft wraps backwards to the last tab', () => {
    const tabs = createTabs(root, buttons, { onChange: vi.fn() });
    tabs.select('day');
    buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    expect(document.activeElement).toBe(buttons[2]);
  });

  it('sync updates the visuals without firing onChange', () => {
    const onChange = vi.fn();
    const tabs = createTabs(root, buttons, { onChange });
    tabs.sync('week');
    expect(tabs.current()).toBe('week');
    expect(buttons[1].classList.contains('active')).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('selecting an unknown value is a no-op', () => {
    const onChange = vi.fn();
    const tabs = createTabs(root, buttons, { onChange });
    tabs.select('year' as 'day');
    expect(tabs.current()).toBe('day');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('destroy stops responding to clicks', () => {
    const onChange = vi.fn();
    const tabs = createTabs(root, buttons, { onChange });
    tabs.destroy();
    buttons[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
