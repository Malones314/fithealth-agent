import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createToaster } from './toast';

let host: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = '<div id="chat"></div>';
  host = document.querySelector<HTMLElement>('#chat') as HTMLElement;
});

describe('components/toast', () => {
  it('keeps the frozen notification classes so the visuals do not shift', () => {
    const toaster = createToaster(host);
    expect(toaster.success('已保存').className).toBe('msg system notify-saved');
    expect(toaster.info('已跳过').className).toBe('msg system notify-skipped');
    expect(toaster.error('失败').className).toBe('msg system notify-error');
  });

  it('announces errors as alert and others as status', () => {
    const toaster = createToaster(host);
    expect(toaster.error('失败').getAttribute('role')).toBe('alert');
    expect(toaster.success('好了').getAttribute('role')).toBe('status');
  });

  it('renders the message as text, never as markup', () => {
    const toaster = createToaster(host);
    const element = toaster.error('<img src=x onerror=alert(1)>');
    expect(element.querySelector('img')).toBeNull();
    expect(element.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('renders the summary block as text too', () => {
    const toaster = createToaster(host);
    const element = toaster.success('已退出', { summary: '<script>bad()</script>训练顺利' });
    const block = element.querySelector('.summary-block');
    expect(block).not.toBeNull();
    expect(block?.querySelector('script')).toBeNull();
    expect(block?.textContent).toContain('已保存摘要');
    expect(block?.textContent).toContain('训练顺利');
  });

  it('omits the summary block when there is no summary', () => {
    const toaster = createToaster(host);
    expect(toaster.info('普通提示').querySelector('.summary-block')).toBeNull();
  });

  it('appends into the host in order and notifies the scroll hook', () => {
    const onAppend = vi.fn();
    const toaster = createToaster(host, onAppend);
    toaster.info('第一条');
    toaster.info('第二条');
    expect(onAppend).toHaveBeenCalledTimes(2);
    expect(Array.from(host.children).map((node) => node.textContent)).toEqual(['第一条', '第二条']);
  });

  it('defaults an unknown level to info', () => {
    const toaster = createToaster(host);
    expect(toaster.show('无级别').className).toContain('notify-skipped');
  });
});
