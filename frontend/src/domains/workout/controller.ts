import { isWorkoutConflict, workoutApi } from '../../api/workout';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { DomainContext, DomainController } from '../domain-factory';
import { clearWorkout, createWorkoutState, loadWorkoutSnapshot } from './state';
import type { WorkoutSnapshot } from './types';
import { createWorkoutView } from './view';

export interface WorkoutDependencies {
  api: typeof workoutApi;
}

const defaults: WorkoutDependencies = {
  api: workoutApi,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败';
}

function editableSets(workout: WorkoutSnapshot): WorkoutSnapshot['sets'] {
  return (workout.sets ?? []).filter(
    (segment) => !segment.is_rest && segment.segment_type === 'set_active',
  );
}

export function createWorkoutController(
  context: DomainContext,
  dependencies: WorkoutDependencies = defaults,
): DomainController {
  const state = createWorkoutState();
  const view = createWorkoutView(context.document);
  const disposers: Array<() => void> = [];
  let started = false;

  function applyResponse(response: unknown): void {
    if (!isRecord(response)) return;
    if (response.has_workout && isRecord(response.workout)) {
      loadWorkoutSnapshot(state, response.workout as WorkoutSnapshot, 'pending');
      state.confirmation = isRecord(response.confirmation) ? response.confirmation : null;
      state.editHistory = isRecord(response.edit_history)
        ? {
            can_undo: Boolean(response.edit_history.can_undo),
            can_restore_parsed_source: Boolean(response.edit_history.can_restore_parsed_source),
          }
        : { can_undo: false, can_restore_parsed_source: false };
      view.render(state);
      context.events.emit('workout:status', { hasWorkout: true, mode: 'pending' });
      if (response.restored)
        context.shell.toast.success('已恢复上次未确认的训练，请检查后继续编辑或保存。');
      if (isRecord(response.restore_issue) && response.restore_issue.message) {
        context.shell.toast.error(String(response.restore_issue.message));
      }
    } else {
      clearWorkout(state);
      view.render(state);
      context.events.emit('workout:status', { hasWorkout: false, mode: 'none' });
      if (isRecord(response.restore_issue)) {
        context.shell.toast.error(
          String(response.restore_issue.message || '上次未确认的训练无法恢复。'),
        );
      }
    }
  }

  async function refresh(force = false): Promise<void> {
    if (state.mode === 'saved') return;
    if (!force && (state.status === 'saving' || view.activeEditor())) return;
    const operation = context.operations.begin('workout-state');
    if (!state.draft) state.status = 'loading';
    try {
      const response = await dependencies.api.state(operation.signal);
      if (operation.isCurrent()) applyResponse(response);
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.status = 'error';
        state.error = errorMessage(error);
      }
    }
  }

  async function update(action: string, payload: Record<string, unknown> = {}): Promise<unknown> {
    const operation = context.operations.begin(`workout-${action}`);
    return dependencies.api.update(action, payload, operation.signal);
  }

  async function recover(action: string, question: string, success: string): Promise<void> {
    if (!state.draft || !context.shell.ask(question)) return;
    view.setBusy(true);
    try {
      await update(action);
      await refresh(true);
      context.shell.toast.success(success);
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(`恢复失败：${errorMessage(error)}`);
    } finally {
      view.setBusy(false);
    }
  }

  async function discard(): Promise<void> {
    if (!state.draft) return;
    if (state.mode === 'saved') {
      if (!context.shell.ask('放弃这次训练记录修改吗？', '未保存的修改将丢失。')) return;
      await loadSavedRecord(String(state.savedRecordId), false);
      return;
    }
    if (!context.shell.ask('确定丢弃这次待确认训练吗？', '此操作不可撤销。')) return;
    view.setBusy(true);
    try {
      await update('clear');
      clearWorkout(state);
      view.render(state);
      context.events.emit('workout:cleared');
      context.shell.toast.info('已丢弃这次导入的训练，未保存。');
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(`丢弃失败：${errorMessage(error)}`);
    } finally {
      view.setBusy(false);
    }
  }

  async function confirm(): Promise<void> {
    if (!state.draft || state.status === 'saving' || context.state.session === 'ended') return;
    if (state.mode === 'saved') {
      await saveSavedRecord();
      return;
    }
    if (!state.confirmation) {
      await refresh(true);
      if (!state.confirmation) {
        context.shell.toast.error('无法取得当前训练的确认凭据，请刷新页面后重试。');
        return;
      }
    }
    if (!context.shell.ask('确认保存训练吗？', '当前所有修改和训练感受将一起保存。')) return;
    state.status = 'saving';
    view.setBusy(true);
    try {
      const result = await update('confirm_with_updates', {
        updates: editableSets(state.draft),
        note: state.draft.note ?? '',
        ...state.confirmation,
      });
      clearWorkout(state);
      view.render(state);
      context.events.emit('workout:cleared');
      const response = isRecord(result) ? result : {};
      context.shell.toast.success(
        response.duplicate_training
          ? '检测到相同 FIT 内容，该训练已存在，未重复保存。'
          : `已保存 ${String(response.sets_count ?? '')} 组训练到 ${String(response.date ?? '')}`,
      );
    } catch (error) {
      if (isWorkoutConflict(error)) {
        state.status = 'conflict';
        state.error = errorMessage(error);
        view.render(state);
        context.shell.toast.error('保存冲突：当前编辑仍保留，请刷新后重试。');
      } else if (!isAbort(error)) {
        state.status = 'editing';
        context.shell.toast.error(`保存失败：${errorMessage(error)}`);
      }
    } finally {
      view.setBusy(false);
    }
  }

  async function loadSavedRecord(recordId: string, showNotice = true): Promise<void> {
    if (
      !recordId ||
      (state.mode === 'saved' && state.savedRecordId === recordId && view.activeEditor())
    )
      return;
    context.operations.cancel('workout-state');
    const operation = context.operations.begin('workout-saved-record');
    try {
      const response = await dependencies.api.savedRecord(recordId, operation.signal);
      if (!operation.isCurrent() || !isRecord(response) || !isRecord(response.record)) return;
      const record = response.record;
      loadWorkoutSnapshot(
        state,
        {
          ...record,
          sets: Array.isArray(record.segments) ? record.segments : [],
        } as WorkoutSnapshot,
        'saved',
      );
      state.savedRecordId = recordId;
      state.revision = Number(response.revision);
      state.confirmation = null;
      state.editHistory = { can_undo: false, can_restore_parsed_source: false };
      view.render(state);
      context.events.emit('workout:status', { hasWorkout: true, mode: 'saved', recordId });
      if (showNotice)
        context.shell.toast.success(`正在编辑已保存训练：${String(record.name || '未命名训练')}`);
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent())
        context.shell.toast.error(`读取训练记录失败：${errorMessage(error)}`);
    }
  }

  async function saveSavedRecord(extra: Record<string, unknown> = {}): Promise<boolean> {
    const recordId = state.savedRecordId;
    const revision = state.revision;
    if (!state.draft || !recordId || !Number.isInteger(revision) || Number(revision) < 1) {
      context.shell.toast.error('历史训练记录编辑状态已失效，请重新选择记录后再保存。');
      return false;
    }
    if (!context.shell.ask('保存训练记录修改吗？', '修改会覆盖这条历史记录的当前版本。'))
      return false;
    state.status = 'saving';
    view.setBusy(true);
    const operation = context.operations.begin('workout-save-record');
    try {
      const response = await dependencies.api.updateSavedRecord(
        recordId,
        Number(revision),
        {
          updates: editableSets(state.draft),
          note: String(state.draft.note ?? '').trim(),
          ...extra,
        },
        operation.signal,
      );
      if (!operation.isCurrent()) return false;
      state.revision = Number(response.revision || Number(revision) + 1);
      state.snapshot = structuredClone(state.draft);
      state.status = 'editing';
      view.render(state);
      context.shell.toast.success('训练记录修改已保存。');
      return true;
    } catch (error) {
      if (isWorkoutConflict(error)) {
        state.status = 'conflict';
        state.error = errorMessage(error);
        view.render(state);
        context.shell.toast.error('保存冲突：其他页面已修改这条记录，当前编辑仍保留。');
      } else if (!isAbort(error)) {
        state.status = 'editing';
        context.shell.toast.error(`保存失败：${errorMessage(error)}`);
      }
      return false;
    } finally {
      view.setBusy(false);
    }
  }

  async function mergeSelected(): Promise<void> {
    const indices = [...state.selected];
    if (indices.length < 2) return;
    if (state.mode === 'saved') {
      await saveSavedRecord({ merge_indices: indices });
      return;
    }
    await update('merge_sets', { indices });
    await refresh(true);
  }

  async function renameSelected(): Promise<void> {
    const indices = [...state.selected];
    if (!indices.length) return;
    const category = context.document.defaultView?.prompt('统一动作名称', '')?.trim();
    if (!category) return;
    if (state.mode === 'saved') {
      if (await saveSavedRecord({ rename_indices: indices, rename_category: category })) {
        state.selected.clear();
        view.render(state);
      }
      return;
    }
    await update('rename_sets', { indices, category });
    await refresh(true);
  }

  async function noticeQuarantined(): Promise<void> {
    try {
      const response = await dependencies.api.quarantined(false);
      if (!isRecord(response) || !Array.isArray(response.files)) return;
      const files = response.files.filter(
        (item) =>
          isRecord(item) &&
          item.recoverable &&
          (Number(item.segments || 0) > 0 || Number(item.hr_records || 0) > 0),
      );
      if (files.length) {
        context.shell.toast.info(
          `有 ${files.length} 份被隔离的未确认训练可以恢复，请在数据管理中处理。`,
        );
      }
    } catch {
      // Startup notice is intentionally non-blocking.
    }
  }

  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onConfirm: () => void confirm(),
          onDiscard: () => void discard(),
          onClear: () => void discard(),
          onUndo: () =>
            void recover('undo_last_edit', '撤销上一次训练编辑吗？', '已撤销上一次训练编辑。'),
          onRestore: () =>
            void recover(
              'restore_parsed_source',
              '恢复 FIT 原始解析结果吗？',
              '已恢复 FIT 原始解析结果。',
            ),
          onRename: () => void renameSelected(),
          onMerge: () => void mergeSelected(),
          onDraftChanged: (draft) => {
            state.draft = draft;
            state.status = 'editing';
          },
        }),
        context.events.on('startup', () => {
          void refresh(true).then(() => {
            if (!state.draft) void noticeQuarantined();
          });
        }),
        context.events.on('workout:loaded', (workout) => {
          context.operations.cancel('workout-saved-record');
          loadWorkoutSnapshot(state, workout as WorkoutSnapshot, 'pending');
          view.render(state);
          void refresh(true);
        }),
        context.events.on('workout:refresh', () => void refresh(true)),
        context.events.on(
          'workout:edit-saved',
          ({ recordId, showNotice }) => void loadSavedRecord(recordId, showNotice),
        ),
        context.events.on('workout:visibility', ({ visible }) => view.setVisible(visible)),
      );
      view.render(state);
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      context.operations.cancel('workout-state');
      context.operations.cancel('workout-saved-record');
      context.operations.cancel('workout-save-record');
    },
  };
}
