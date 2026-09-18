import { beforeEach, describe, expect, it } from 'vitest';
import { CHUNK_SIZE, CHUNK_THRESHOLD, renderChunkedList } from './chunked-list';

let host: HTMLElement;

function row(item: string): HTMLElement {
  const element = document.createElement('div');
  element.className = 'data-row';
  element.textContent = item;
  return element;
}

const items = (count: number): string[] =>
  Array.from({ length: count }, (_, index) => `记录 ${index}`);

const rowCount = (): number => host.querySelectorAll('.data-row').length;
const moreButton = (): HTMLButtonElement | null =>
  host.querySelector<HTMLButtonElement>('.chunked-more');

beforeEach(() => {
  document.body.innerHTML = '<div id="list"></div>';
  host = document.querySelector<HTMLElement>('#list') as HTMLElement;
});

describe('components/chunked-list', () => {
  it('renders a short list in full, with no extra button', () => {
    renderChunkedList(host, items(30), row);
    expect(rowCount()).toBe(30);
    expect(moreButton()).toBeNull();
  });

  it('renders everything right at the threshold', () => {
    renderChunkedList(host, items(CHUNK_THRESHOLD), row);
    expect(rowCount()).toBe(CHUNK_THRESHOLD);
    expect(moreButton()).toBeNull();
  });

  it('splits into batches once past the threshold', () => {
    const handle = renderChunkedList(host, items(CHUNK_THRESHOLD + 1), row);
    expect(handle.rendered()).toBe(CHUNK_SIZE);
    expect(rowCount()).toBe(CHUNK_SIZE);
    expect(moreButton()).not.toBeNull();
  });

  it('reports the remaining count on the button', () => {
    renderChunkedList(host, items(450), row);
    expect(moreButton()?.textContent).toContain('350');
  });

  it('clicking the button appends the next batch and keeps the button last', () => {
    renderChunkedList(host, items(450), row);
    moreButton()?.click();
    expect(rowCount()).toBe(200);
    // 按钮必须留在末尾，否则后续批次会渲染到它后面。
    expect(host.lastElementChild).toBe(moreButton());
  });

  it('the button disappears once everything is rendered', () => {
    // 250 条：首批 100，之后每次 100，所以要点两次才渲染完。
    renderChunkedList(host, items(250), row);
    moreButton()?.click();
    expect(rowCount()).toBe(200);
    expect(moreButton()).not.toBeNull();
    moreButton()?.click();
    expect(rowCount()).toBe(250);
    expect(moreButton()).toBeNull();
  });

  it('more() reports whether anything is left', () => {
    const handle = renderChunkedList(host, items(250), row);
    expect(handle.more()).toBe(true);
    expect(handle.rendered()).toBe(200);
    expect(handle.more()).toBe(false);
    expect(handle.rendered()).toBe(250);
  });

  it('all() renders the remainder in one go', () => {
    const handle = renderChunkedList(host, items(1000), row);
    handle.all();
    expect(handle.rendered()).toBe(1000);
    expect(rowCount()).toBe(1000);
    expect(moreButton()).toBeNull();
  });

  it('re-rendering replaces the previous content instead of stacking', () => {
    renderChunkedList(host, items(300), row);
    renderChunkedList(host, items(5), row);
    expect(rowCount()).toBe(5);
    expect(moreButton()).toBeNull();
  });

  it('renders an empty list without a button', () => {
    renderChunkedList(host, [], row);
    expect(rowCount()).toBe(0);
    expect(moreButton()).toBeNull();
  });

  it('passes the real index through, so ordering survives batching', () => {
    const handle = renderChunkedList(host, items(250), (item, index) => row(`${index}:${item}`));
    const first = host.querySelector('.data-row');
    expect(first?.textContent).toBe('0:记录 0');
    handle.all();
    const rows = host.querySelectorAll('.data-row');
    expect(rows[rows.length - 1].textContent).toBe('249:记录 249');
  });

  it('honours custom threshold and chunk size', () => {
    const handle = renderChunkedList(host, items(20), row, { threshold: 5, chunkSize: 4 });
    expect(handle.rendered()).toBe(4);
    handle.more();
    expect(handle.rendered()).toBe(8);
  });
});
