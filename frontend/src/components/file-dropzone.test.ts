import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createDropzone, validateBatch } from './file-dropzone';

function file(name: string, size = 10, type = ''): File {
  const handle = new File(['x'], name, { type });
  Object.defineProperty(handle, 'size', { value: size });
  return handle;
}

function dragEvent(type: string, files: File[] = [], types = ['Files']): DragEvent {
  const event = new Event(type, { bubbles: true, cancelable: true }) as DragEvent;
  Object.defineProperty(event, 'dataTransfer', {
    value: { types, files, dropEffect: 'none' },
  });
  return event;
}

let root: HTMLElement;

beforeEach(() => {
  document.body.innerHTML = '<div class="chat-panel" id="panel"></div>';
  root = document.querySelector<HTMLElement>('#panel') as HTMLElement;
});

describe('components/file-dropzone validation', () => {
  it('rejects a batch over the file count limit', () => {
    expect(validateBatch([file('a.zip'), file('b.zip')], { maxFiles: 1 })).toContain('最多');
  });

  it('rejects a file over the size limit', () => {
    expect(validateBatch([file('big.fit', 99)], { maxBytes: 10 })).toContain('big.fit');
  });

  it('rejects an unsupported extension', () => {
    expect(validateBatch([file('notes.pdf')], { accept: ['.fit', '.zip'] })).toContain('notes.pdf');
  });

  it('accepts a file matching the extension list', () => {
    expect(validateBatch([file('ride.fit')], { accept: ['.fit', '.zip'] })).toBeNull();
  });

  it('accepts a file matching the MIME list', () => {
    expect(
      validateBatch([file('plate.jpg', 10, 'image/jpeg')], { accept: ['image/jpeg'] }),
    ).toBeNull();
  });

  it('rejects an empty batch', () => {
    expect(validateBatch([], {})).toContain('至少');
  });
});

describe('components/file-dropzone drag behaviour', () => {
  it('nested dragenter/dragleave keeps the active class until the last leave', () => {
    createDropzone(root, { onFiles: vi.fn() });
    root.dispatchEvent(dragEvent('dragenter'));
    root.dispatchEvent(dragEvent('dragenter'));
    expect(root.classList.contains('dragging')).toBe(true);
    root.dispatchEvent(dragEvent('dragleave'));
    expect(root.classList.contains('dragging')).toBe(true);
    root.dispatchEvent(dragEvent('dragleave'));
    expect(root.classList.contains('dragging')).toBe(false);
  });

  it('ignores drags that carry no files', () => {
    createDropzone(root, { onFiles: vi.fn() });
    root.dispatchEvent(dragEvent('dragenter', [], ['text/plain']));
    expect(root.classList.contains('dragging')).toBe(false);
  });

  it('drop hands validated files to onFiles and clears the active class', () => {
    const onFiles = vi.fn();
    createDropzone(root, { onFiles, accept: ['.fit'] });
    root.dispatchEvent(dragEvent('dragenter'));
    root.dispatchEvent(dragEvent('drop', [file('ride.fit')]));
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0][0].name).toBe('ride.fit');
    expect(root.classList.contains('dragging')).toBe(false);
  });

  it('a rejected drop reports the reason and never reaches onFiles', () => {
    const onFiles = vi.fn();
    const onReject = vi.fn();
    createDropzone(root, { onFiles, onReject, accept: ['.fit'] });
    root.dispatchEvent(dragEvent('drop', [file('notes.pdf')]));
    expect(onFiles).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onReject.mock.calls[0][0]).toContain('notes.pdf');
  });

  it('disabled dropzones ignore both drag and drop', () => {
    const onFiles = vi.fn();
    createDropzone(root, { onFiles, enabled: () => false });
    root.dispatchEvent(dragEvent('dragenter'));
    root.dispatchEvent(dragEvent('drop', [file('ride.fit')]));
    expect(root.classList.contains('dragging')).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
  });

  it('destroy removes the listeners', () => {
    const onFiles = vi.fn();
    const zone = createDropzone(root, { onFiles });
    zone.destroy();
    root.dispatchEvent(dragEvent('drop', [file('ride.fit')]));
    expect(onFiles).not.toHaveBeenCalled();
  });

  it('a bound input shares the same validation path and resets its value', () => {
    document.body.insertAdjacentHTML('beforeend', '<input id="picker" type="file" />');
    const input = document.querySelector<HTMLInputElement>('#picker') as HTMLInputElement;
    const onFiles = vi.fn();
    const zone = createDropzone(root, { onFiles, accept: ['.fit'] });
    zone.bindInput(input);
    Object.defineProperty(input, 'files', { value: [file('ride.fit')], configurable: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onFiles).toHaveBeenCalledTimes(1);
    // 复位后连续选同一个文件仍会触发 change。
    expect(input.value).toBe('');
  });
});
