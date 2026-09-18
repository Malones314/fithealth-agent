/**
 * 统一文件入口：拖拽、按钮触发的 <input type=file>，以及两者共用的校验。
 *
 * legacy 把 dragenter/dragover/dragleave/drop 手写在 chat panel 上，用一个
 * `chatDragDepth` 计数器抵消子元素冒泡；每个文件入口（FIT、健康 ZIP/CSV、餐盘
 * 图片、备份 ZIP）各自重复一遍"取 files → 校验后缀 → 校验大小 → 清空 input.value"。
 * 这里把 depth 计数、`dropEffect`、input 复位和校验流程收敛一次。
 */

import { validateUpload } from '../shared/validation';

export interface DropzoneRule {
  /** 允许的扩展名或 MIME，交给 shared/validation 统一判断。 */
  accept?: readonly string[];
  /** 单文件大小上限（字节）。 */
  maxBytes?: number;
  /** 一次最多几个文件。 */
  maxFiles?: number;
  /** 至少几个文件，默认 1。 */
  minFiles?: number;
}

export interface DropzoneOptions extends DropzoneRule {
  /** 拖拽激活时加在 root 上的 class，默认 `dragging`。 */
  activeClass?: string;
  /** 返回 false 时忽略这次拖拽/选择（例如会话已结束）。 */
  enabled?: () => boolean;
  onFiles(files: File[]): void | Promise<void>;
  onReject?(reason: string, files: File[]): void;
}

export interface DropzoneHandle {
  /** 供按钮触发的 input：选择后走同一条校验流程。 */
  bindInput(input: HTMLInputElement): void;
  /** 直接投喂一批文件，走同一条校验流程（供程序化调用与测试使用）。 */
  accept(files: ArrayLike<File>): void;
  destroy(): void;
}

export function validateBatch(files: File[], rule: DropzoneRule): string | null {
  const minFiles = rule.minFiles ?? 1;
  if (files.length < minFiles) return `请至少选择 ${minFiles} 个文件`;
  if (rule.maxFiles !== undefined && files.length > rule.maxFiles) {
    return `每次最多选择 ${rule.maxFiles} 个文件`;
  }
  for (const file of files) {
    if (rule.maxBytes !== undefined && file.size > rule.maxBytes) {
      return `${file.name} 超过大小限制`;
    }
    try {
      validateUpload(file, rule.accept ?? []);
    } catch (error) {
      return error instanceof Error ? error.message : `不支持的文件：${file.name}`;
    }
  }
  return null;
}

export function createDropzone(root: HTMLElement, options: DropzoneOptions): DropzoneHandle {
  const activeClass = options.activeClass ?? 'dragging';
  const inputs: Array<[HTMLInputElement, (event: Event) => void]> = [];
  let depth = 0;

  const enabled = (): boolean => options.enabled?.() !== false;

  const carriesFiles = (event: DragEvent): boolean =>
    Boolean(event.dataTransfer?.types.includes('Files'));

  function accept(list: ArrayLike<File>): void {
    const files = Array.from(list);
    if (files.length === 0 || !enabled()) return;
    const rejection = validateBatch(files, options);
    if (rejection) {
      options.onReject?.(rejection, files);
      return;
    }
    void options.onFiles(files);
  }

  const onDragEnter = (event: DragEvent): void => {
    if (!carriesFiles(event) || !enabled()) return;
    event.preventDefault();
    depth += 1;
    root.classList.add(activeClass);
  };
  const onDragOver = (event: DragEvent): void => {
    if (!carriesFiles(event) || !enabled()) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  };
  const onDragLeave = (event: DragEvent): void => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    depth = Math.max(0, depth - 1);
    if (depth === 0) root.classList.remove(activeClass);
  };
  const onDrop = (event: DragEvent): void => {
    const files = event.dataTransfer?.files;
    if (!files?.length || !enabled()) return;
    event.preventDefault();
    depth = 0;
    root.classList.remove(activeClass);
    accept(files);
  };

  root.addEventListener('dragenter', onDragEnter);
  root.addEventListener('dragover', onDragOver);
  root.addEventListener('dragleave', onDragLeave);
  root.addEventListener('drop', onDrop);

  return {
    accept,
    bindInput(input) {
      const listener = (event: Event): void => {
        const target = event.target as HTMLInputElement;
        const picked = target.files;
        if (picked) accept(picked);
        // 复位，否则连续选同一个文件不会再触发 change。
        target.value = '';
      };
      input.addEventListener('change', listener);
      inputs.push([input, listener]);
    },
    destroy() {
      root.removeEventListener('dragenter', onDragEnter);
      root.removeEventListener('dragover', onDragOver);
      root.removeEventListener('dragleave', onDragLeave);
      root.removeEventListener('drop', onDrop);
      inputs.forEach(([input, listener]) => input.removeEventListener('change', listener));
      inputs.length = 0;
    },
  };
}
