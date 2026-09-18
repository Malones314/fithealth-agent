import type { AppShell } from '../../app/shell';
import type { ActivityChoice, UploadDomainState } from './types';

export interface UploadViewHandlers {
  onFiles(files: File[]): void;
  onFood(file: File): void;
  onRemoveFood(): void;
  onActivity(activity: ActivityChoice): void;
  onClosePicker(): void;
}

export interface UploadView {
  bind(handlers: UploadViewHandlers): () => void;
  render(state: UploadDomainState): void;
  renderActivities(state: UploadDomainState): void;
}

function required<T extends Element>(document: Document, selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Upload element not found: ${selector}`);
  return element;
}

export function createUploadsView(document: Document, shell: AppShell): UploadView {
  const input = required<HTMLInputElement>(document, '#file-input');
  const button = required<HTMLButtonElement>(document, '#file-upload-button');
  const foodInput = required<HTMLInputElement>(document, '#food-image-input');
  const foodButton = required<HTMLButtonElement>(document, '#food-image-button');
  const foodAttachment = required<HTMLElement>(document, '#food-attachment');
  const foodName = required<HTMLElement>(document, '#food-attachment-name');
  const foodRemove = required<HTMLButtonElement>(document, '#food-attachment-remove');
  const pickerList = required<HTMLElement>(document, '#activity-picker-list');
  const pickerClose = required<HTMLButtonElement>(document, '#activity-picker-close');
  const chatPanel = required<HTMLElement>(document, '.chat-panel');
  const listeners: Array<[EventTarget, string, EventListener]> = [];
  let activityHandler: UploadViewHandlers['onActivity'] = () => undefined;
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };
  let dropzone: ReturnType<AppShell['dropzone']> | null = null;

  return {
    bind(handlers) {
      activityHandler = handlers.onActivity;
      listen(button, 'click', () => input.click());
      listen(input, 'change', () => {
        handlers.onFiles(Array.from(input.files ?? []));
        input.value = '';
      });
      listen(foodButton, 'click', () => foodInput.click());
      listen(foodInput, 'change', () => {
        const file = foodInput.files?.[0];
        if (file) handlers.onFood(file);
        foodInput.value = '';
      });
      listen(foodRemove, 'click', handlers.onRemoveFood);
      listen(pickerClose, 'click', handlers.onClosePicker);
      dropzone = shell.dropzone(chatPanel, {
        onFiles: (files) => handlers.onFiles(files),
      });
      return () => {
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
        dropzone?.destroy();
      };
    },
    render(state) {
      document.documentElement.dataset.uploadActive = String(state.active);
      button.className =
        state.status === 'idle' ? '' : state.status === 'error' ? 'err' : state.status;
      button.disabled = state.status === 'uploading';
      input.disabled = state.status === 'uploading';
      button.title =
        state.error ||
        (state.status === 'uploading' ? '解析中，请稍候…' : '上传训练、健康数据或计划');
      button.setAttribute('aria-label', button.title);
      foodAttachment.classList.toggle('show', Boolean(state.pendingFoodImage));
      foodName.textContent = state.pendingFoodImage?.name ?? '';
    },
    renderActivities(state) {
      const multiple = new Set(state.activities.map((item) => item.zip)).size > 1;
      pickerList.replaceChildren(
        ...state.activities.map((activity) => {
          const item = document.createElement('button');
          item.type = 'button';
          item.className = 'activity-picker-item';
          const label = document.createElement('span');
          label.textContent = multiple ? `${activity.zip} › ${activity.name}` : activity.name;
          const action = document.createElement('b');
          action.textContent = '编辑';
          item.append(label, action);
          item.addEventListener('click', () => activityHandler(activity));
          return item;
        }),
      );
      if (state.activities.length) shell.openModal('activityPicker');
      else shell.closeModal('activityPicker');
    },
  };
}
