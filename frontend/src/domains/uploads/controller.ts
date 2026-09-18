import { uploadsApi } from '../../api/uploads';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { DomainContext, DomainController } from '../domain-factory';
import { activityKey, completeActivity, createUploadsState, setActivities } from './state';
import type { ActivityChoice } from './types';
import { createUploadsView } from './view';

export interface UploadDependencies {
  api: typeof uploadsApi;
}

const defaults: UploadDependencies = { api: uploadsApi };
const MiB = 1024 * 1024;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '上传失败';
}

export function createUploadsController(
  context: DomainContext,
  dependencies: UploadDependencies = defaults,
): DomainController {
  const state = createUploadsState();
  const view = createUploadsView(context.document, context.shell);
  const disposers: Array<() => void> = [];
  let started = false;
  let hasPendingWorkout = false;

  function setStatus(status: typeof state.status, error?: string): void {
    state.status = status;
    state.active = status === 'uploading';
    state.error = error;
    view.render(state);
  }

  function publishMessage(text: unknown): void {
    if (typeof text === 'string' && text)
      context.events.emit('chat:message', { role: 'bot', text });
  }

  async function uploadPlan(file: File): Promise<void> {
    let confirmLarge = false;
    if (file.size > MiB) throw new Error('训练计划文件不能超过 1 MiB');
    if (file.size > 256 * 1024) {
      confirmLarge = context.shell.ask('继续上传较大的训练计划吗？', '大文件会占用更多解析时间。');
      if (!confirmLarge) return;
    }
    const operation = context.operations.begin('upload-plan');
    const result = await dependencies.api.plan(file, confirmLarge, operation.signal);
    if (!operation.isCurrent()) return;
    if (result.status === 'manual_confirmation_required') {
      if (!context.shell.ask('仍按训练计划纳管吗？', '当前外部模型已关闭，无法自动鉴定。')) return;
    } else if (result.status !== 'ok' || result.valid === false) {
      throw new Error(String(result.message || result.reason || '非训练计划文件'));
    }
    const subject = String(result.subject || '综合训练');
    context.events.emit('chat:submit-plan', { content: String(result.content || ''), subject });
    setStatus('done');
  }

  async function uploadFit(file: File): Promise<void> {
    if (file.size > 50 * MiB) throw new Error('FIT 文件不能超过 50 MiB');
    let overwritePending = false;
    if (hasPendingWorkout) {
      overwritePending = context.shell.ask(
        '覆盖当前待确认训练吗？',
        '未保存的训练草稿将被新 FIT 替换。',
      );
      if (!overwritePending) {
        setStatus('idle');
        return;
      }
    }
    const operation = context.operations.begin('upload-fit');
    const result = await dependencies.api.fit(file, overwritePending, operation.signal);
    if (!operation.isCurrent()) return;
    if (result.status !== 'ok') throw new Error(String(result.message || '未解析出训练分段'));
    publishMessage(result.message);
    if (isRecord(result.workout)) context.events.emit('workout:loaded', result.workout);
    setStatus('done');
  }

  async function uploadHealth(files: File[]): Promise<void> {
    if (!files.length || files.length > 5) throw new Error('每次请选择 1-5 个健康数据文件');
    for (const file of files) {
      const lower = file.name.toLowerCase();
      if (!lower.endsWith('.zip') && !lower.endsWith('.csv'))
        throw new Error('健康数据批次只能包含 .zip 或 .csv');
      if (file.size > (lower.endsWith('.zip') ? 50 * MiB : 2 * MiB))
        throw new Error(`${file.name} 超过大小限制`);
    }
    const operation = context.operations.begin('upload-health');
    const result = await dependencies.api.healthBatch(files, operation.signal);
    if (!operation.isCurrent()) return;
    if (result.status === 'error') throw new Error(String(result.message || '健康数据导入失败'));
    publishMessage(result.message || '健康数据已解析并保存。');
    if (isRecord(result.workout)) {
      context.events.emit('workout:loaded', result.workout);
    } else if (Array.isArray(result.activities)) {
      const activities = result.activities
        .filter(isRecord)
        .map((item) => ({ ...item, zip: String(item.zip || ''), name: String(item.name || '') }))
        .filter((item) => item.zip && item.name);
      setActivities(state, files, activities);
      view.renderActivities(state);
    }
    context.events.emit('health:refresh');
    setStatus(result.status === 'partial' ? 'error' : 'done');
  }

  async function handleFiles(files: File[]): Promise<void> {
    if (!files.length || state.active || context.state.session === 'ended') return;
    setStatus('uploading');
    try {
      const health =
        files.length > 1 ||
        files[0].name.toLowerCase().endsWith('.zip') ||
        files[0].name.toLowerCase().endsWith('.csv');
      if (health) await uploadHealth(files);
      else if (/\.(md|txt)$/i.test(files[0].name)) await uploadPlan(files[0]);
      else if (/\.fit$/i.test(files[0].name)) await uploadFit(files[0]);
      else throw new Error('请选择 .fit、.zip、.csv、.md 或 .txt 格式的文件');
    } catch (error) {
      if (!isAbort(error)) {
        setStatus('error', errorMessage(error));
        context.shell.toast.error(errorMessage(error));
      }
    }
  }

  async function loadActivity(activity: ActivityChoice): Promise<void> {
    const file = state.zipFiles.get(activity.zip);
    if (!file) return;
    const operation = context.operations.begin('upload-health-activity');
    setStatus('uploading');
    try {
      const result = await dependencies.api.healthActivity(
        file,
        activity.zip,
        activity.name,
        operation.signal,
      );
      if (!operation.isCurrent()) return;
      if (!isRecord(result.workout)) throw new Error(String(result.message || '活动载入失败'));
      state.activeActivity = activityKey(activity);
      completeActivity(state, activity);
      context.events.emit('workout:loaded', result.workout);
      view.renderActivities(state);
      setStatus('done');
    } catch (error) {
      if (!isAbort(error)) setStatus('error', errorMessage(error));
    }
  }

  async function analyzeFood(file: File): Promise<void> {
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type) || file.size > 10 * MiB) {
      context.shell.toast.error('餐盘照片仅支持 JPEG、PNG、WebP，且不能超过 10 MiB');
      return;
    }
    const operation = context.operations.begin('upload-food');
    state.pendingFoodImage = file;
    view.render(state);
    try {
      const result = await dependencies.api.food(file, operation.signal);
      if (!operation.isCurrent()) return;
      context.events.emit('checkin:food', result);
      publishMessage(result.message || '餐盘营养估算已完成。');
      state.pendingFoodImage = undefined;
      view.render(state);
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(errorMessage(error));
    }
  }

  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onFiles: (files) => void handleFiles(files),
          onFood: (file) => void analyzeFood(file),
          onRemoveFood: () => {
            state.pendingFoodImage = undefined;
            context.operations.cancel('upload-food');
            view.render(state);
          },
          onActivity: (activity) => void loadActivity(activity),
          onClosePicker: () => {
            context.operations.cancel('upload-health-activity');
            state.activities = [];
            state.activeActivity = undefined;
            setStatus('idle');
            view.renderActivities(state);
            context.shell.closeModal('activityPicker');
          },
        }),
        context.events.on('workout:cleared', () => view.renderActivities(state)),
        context.events.on('workout:status', ({ mode }) => {
          hasPendingWorkout = mode === 'pending';
        }),
      );
      view.render(state);
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      for (const key of [
        'upload-plan',
        'upload-fit',
        'upload-health',
        'upload-health-activity',
        'upload-food',
      ]) {
        context.operations.cancel(key);
      }
    },
  };
}
