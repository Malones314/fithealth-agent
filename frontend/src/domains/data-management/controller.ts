import { healthApi } from '../../api/health';
import { maintenanceApi } from '../../api/maintenance';
import { memoriesApi } from '../../api/memories';
import { plansApi } from '../../api/plans';
import { recordsApi } from '../../api/records';
import { workoutApi } from '../../api/workout';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { JsonObject } from '../../shared/types';
import type { DomainContext, DomainController } from '../domain-factory';
import { createDataManagementState } from './state';
import { createDataManagementView, type DataAction } from './view';
export interface DataManagementDependencies {
  maintenance: typeof maintenanceApi;
  memories: typeof memoriesApi;
  plans: typeof plansApi;
  records: typeof recordsApi;
  workout: typeof workoutApi;
  health: typeof healthApi;
  prompt: (message: string, initial?: string) => string | null;
  reload: () => void;
}
const defaults: DataManagementDependencies = {
  maintenance: maintenanceApi,
  memories: memoriesApi,
  plans: plansApi,
  records: recordsApi,
  workout: workoutApi,
  health: healthApi,
  prompt: (message, initial = '') => window.prompt(message, initial),
  reload: () => window.location.reload(),
};
const message = (error: unknown) => (error instanceof Error ? error.message : '操作失败');
function download(result: { blob: Blob; filename?: string }, fallback: string): void {
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = result.filename ?? fallback;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
export function createDataManagementController(
  context: DomainContext,
  dependencies: DataManagementDependencies = defaults,
): DomainController {
  const state = createDataManagementState();
  const view = createDataManagementView(context.document, context.shell);
  const disposers: Array<() => void> = [];
  let started = false;
  async function run<T>(
    key: string,
    task: (signal: AbortSignal) => Promise<T>,
  ): Promise<T | undefined> {
    const operation = context.operations.begin(`data-${key}`);
    try {
      const value = await task(operation.signal);
      return operation.isCurrent() ? value : undefined;
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        view.status(message(error), 'error');
        context.shell.toast.error(message(error));
      }
      return undefined;
    }
  }
  async function refresh(): Promise<void> {
    state.loading = true;
    view.status();
    const [overview, recovery, quarantine, rawAudit, hrAudit] = await Promise.all([
      run('overview', (signal) => dependencies.maintenance.overview(signal)),
      run('recovery', (signal) => dependencies.maintenance.recoveryPoints(signal)),
      run('quarantine', (signal) => dependencies.workout.quarantined(true, signal)),
      run('raw-audit', (signal) => dependencies.health.rawAudit(signal)),
      run('hr-audit', (signal) => dependencies.maintenance.hrAudit(signal)),
    ]);
    state.loading = false;
    if (overview && isRecord(overview)) {
      state.overview = overview;
      view.render(state);
    }
    view.renderRecovery(
      isRecord(recovery) && Array.isArray(recovery.points) ? recovery.points.filter(isRecord) : [],
    );
    view.renderQuarantine(
      isRecord(quarantine) && Array.isArray(quarantine.files)
        ? quarantine.files.filter(isRecord)
        : [],
    );
    const raw = isRecord(rawAudit) ? rawAudit : {};
    const hr = isRecord(hrAudit) ? hrAudit : {};
    const audit: JsonObject[] = [
      ...(Array.isArray(raw.missing)
        ? raw.missing.map((name) => ({ kind: '健康原始文件缺失', name }))
        : []),
      ...(Array.isArray(raw.orphans)
        ? raw.orphans.map((name) => ({ kind: '孤立健康原始文件', name, orphan: 'health' }))
        : []),
      ...(Array.isArray(hr.missing)
        ? hr.missing.filter(isRecord).map((item) => ({ kind: '训练心率流缺失', name: item.file }))
        : []),
      ...(Array.isArray(hr.orphans)
        ? hr.orphans.map((name) => ({ kind: '孤立训练心率流', name, orphan: 'hr' }))
        : []),
    ];
    view.renderAudit(audit);
  }
  async function viewer(
    mode: 'training' | 'nutrition',
    day: string,
    explicit = false,
  ): Promise<void> {
    const operation = context.operations.begin('data-viewer');
    state.viewerMode = mode;
    state.viewerDay = day;
    context.events.emit('workout:visibility', { visible: mode === 'training' });
    try {
      const result = await dependencies.records[mode](day, operation.signal);
      // Both modes share the same view. Superseded/aborted requests must not
      // clear its items, repaint it, or open an editor for a stale record.
      if (!operation.isCurrent()) return;
      state.viewerItems = Array.isArray(result.items) ? result.items.filter(isRecord) : [];
      view.renderViewer(state);
      if (mode === 'training' && state.viewerItems.length) {
        const item = state.viewerItems[0];
        context.events.emit('workout:edit-saved', {
          recordId: String(item.id),
          showNotice: explicit,
        });
      }
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.viewerItems = [];
        view.renderViewer(state);
        view.status(message(error), 'error');
        context.shell.toast.error(message(error));
      }
    }
  }
  async function action(action: DataAction): Promise<void> {
    if (action === 'import-backup') {
      view.backupInput().click();
      return;
    }
    if (action === 'export-backup') {
      const result = await run('export', (signal) => dependencies.maintenance.exportBackup(signal));
      if (result) download(result, 'fithealth-backup.zip');
      return;
    }
    if (action === 'view-record') {
      const id = view.selectedRecords()[0];
      if (id) {
        const result = await run('record', (signal) =>
          dependencies.records.trainingRecord(id, signal),
        );
        if (result) view.previewRecord(result);
      }
      return;
    }
    if (action === 'delete-records') {
      const ids = view.selectedRecords();
      if (ids.length && context.shell.ask('永久删除所选训练记录吗？', `共 ${ids.length} 条`)) {
        await run('delete-records', (signal) => dependencies.records.deleteBatch(ids, signal));
        await refresh();
      }
      return;
    }
    if (action === 'delete-plans') {
      const ids = view.selectedPlans();
      if (ids.length && context.shell.ask('永久删除所选训练计划吗？', `共 ${ids.length} 份`)) {
        await run('delete-plans', (signal) => dependencies.plans.removeBatch(ids, signal));
        await refresh();
      }
      return;
    }
    if (action === 'clear-memories' && context.shell.ask('永久删除全部临时记忆吗？')) {
      await run('clear-memories', (signal) => dependencies.memories.clear(signal));
      await refresh();
      return;
    }
    if (action === 'delete-pending' && context.shell.ask('永久删除当前待确认训练吗？')) {
      await run('delete-pending', (signal) =>
        dependencies.maintenance.deletePendingWorkout(signal),
      );
      context.events.emit('workout:refresh');
      await refresh();
      return;
    }
    if (action === 'reset-profile' && context.shell.ask('永久清空用户档案并恢复默认器械吗？')) {
      await run('reset-profile', (signal) => dependencies.maintenance.resetProfile(signal));
      await refresh();
      return;
    }
    if (action === 'add-soreness') {
      const region = dependencies.prompt('受伤或酸痛区域', '手臂');
      if (region === null) return;
      const level = dependencies.prompt('程度（recovered/sore/painful）', 'sore');
      if (level === null) return;
      await run('add-soreness', (signal) =>
        dependencies.memories.addSoreness(
          { region: region.trim(), level: level.trim(), evidence: '数据管理手动录入' },
          signal,
        ),
      );
      await refresh();
      return;
    }
    if (action === 'reset-all') {
      const confirmation = view.resetConfirmation();
      if (confirmation !== '删除全部数据') {
        view.status('请输入“删除全部数据”后再执行', 'error');
        return;
      }
      if (!context.shell.ask('永久删除全部本地数据吗？', '删除前会自动生成恢复点。')) return;
      const result = await run('reset', (signal) =>
        dependencies.maintenance.reset(confirmation, signal),
      );
      if (result) {
        context.events.emit('workout:refresh');
        context.events.emit('health:refresh');
        await refresh();
        view.status(
          result.partial ? '部分数据未能删除' : '全部本地数据已删除',
          result.partial ? 'error' : 'success',
        );
        if (result.partial)
          view.renderResetResult(result, (keys) => {
            void retryReset(keys);
          });
      }
    }
  }
  async function retryReset(keys: string[]): Promise<void> {
    if (
      !context.shell.ask(
        '重新删除所选项目的数据吗？',
        '这些项目在上次重置后新增的数据也会删除；继续前会生成新的恢复点。',
      )
    )
      return;
    const result = await run('reset-retry', (signal) =>
      dependencies.maintenance.retryReset(keys, signal),
    );
    if (!result) return;
    context.events.emit('workout:refresh');
    context.events.emit('health:refresh');
    await refresh();
    view.status(
      result.partial ? '部分项目仍失败' : '失败项目已重试成功',
      result.partial ? 'error' : 'success',
    );
    if (result.partial)
      view.renderResetResult(result, (nextKeys) => {
        void retryReset(nextKeys);
      });
  }
  async function plan(
    action: 'view' | 'edit' | 'delete' | 'download',
    item: JsonObject,
  ): Promise<void> {
    const id = String(item.id);
    if (action === 'download') {
      const filename = String(item.filename ?? item.title ?? 'training-plan.md');
      download(
        {
          blob: new Blob([String(item.content ?? '')], {
            type: 'text/markdown;charset=utf-8',
          }),
        },
        filename.endsWith('.md') ? filename : `${filename}.md`,
      );
      return;
    }
    if (action === 'view') {
      const result = await run('plan', (signal) => dependencies.plans.get(id, signal));
      if (result) view.previewPlan(result);
      return;
    }
    if (action === 'delete') {
      if (context.shell.ask(`永久删除训练计划“${String(item.title ?? '')}”吗？`)) {
        await run('plan-delete', (signal) => dependencies.plans.remove(id, signal));
        await refresh();
      }
      return;
    }
    const date = dependencies.prompt('计划日期（YYYY-MM-DD）', String(item.date ?? ''));
    if (date === null) return;
    const subject = dependencies.prompt('训练科目', String(item.subject ?? ''));
    if (subject === null) return;
    const title = dependencies.prompt('计划标题', String(item.title ?? ''));
    if (title === null) return;
    await run('plan-edit', (signal) =>
      dependencies.plans.update(id, { date, subject, title }, signal),
    );
    await refresh();
  }
  async function memory(action: string, memory: JsonObject, fact?: JsonObject): Promise<void> {
    const id = String(memory.id);
    const ref = String(fact?.factRef ?? fact?.fact_id ?? '');
    if (action === 'confirm')
      await run('memory', (signal) => dependencies.memories.confirm(id, signal));
    else if (action === 'delete-daily')
      await run('memory', (signal) => dependencies.records.deleteTraining(id, signal));
    else if (action === 'confirm-fact')
      await run('memory', (signal) => dependencies.memories.confirmFact(id, ref, signal));
    else if (action === 'reject-fact')
      await run('memory', (signal) => dependencies.memories.rejectFact(id, ref, signal));
    else if (action === 'edit-fact') {
      const value = dependencies.prompt('修改事实内容', String(fact?.value ?? ''));
      if (value === null) return;
      await run('memory-edit', (signal) =>
        dependencies.memories.updateFact(id, ref, { value }, signal),
      );
    } else if (
      action === 'rollback-fact' &&
      context.shell.ask('将该事实回滚到上一版本吗？', '回滚后需要重新确认。')
    )
      await run('memory-rollback', (signal) => dependencies.memories.rollbackFact(id, ref, signal));
    else if (action === 'delete')
      await run('memory-delete', (signal) => dependencies.memories.remove(id, signal));
    await refresh();
  }
  async function soreness(action: 'edit' | 'delete', item: JsonObject): Promise<void> {
    const id = String(item.id);
    if (action === 'delete') {
      if (context.shell.ask('删除该酸痛记录吗？'))
        await run('soreness-delete', (signal) => dependencies.memories.removeSoreness(id, signal));
    } else {
      const level = dependencies.prompt(
        '程度（recovered/sore/painful）',
        String(item.level ?? 'sore'),
      );
      if (level !== null)
        await run('soreness-edit', (signal) =>
          dependencies.memories.updateSoreness(id, { level }, signal),
        );
    }
    await refresh();
  }
  async function quarantine(
    action: 'preview' | 'restore' | 'dismiss' | 'delete',
    item: JsonObject,
  ): Promise<void> {
    const name = String(item.name);
    if (action === 'preview') {
      const result = await run('quarantine-preview', (signal) =>
        dependencies.workout.preview(name, signal),
      );
      if (result) context.shell.toast.info(JSON.stringify(result.preview ?? result));
      return;
    }
    if (action === 'restore') {
      const result = await run('quarantine-restore', (signal) =>
        dependencies.workout.restoreQuarantined(name, true, signal),
      );
      if (result && isRecord(result.workout)) {
        context.events.emit('workout:loaded', result.workout);
        context.shell.closeModal('data');
      }
      return;
    }
    if (action === 'dismiss')
      await run('quarantine-dismiss', (signal) =>
        dependencies.workout.dismissQuarantined(name, signal),
      );
    else if (context.shell.ask(`永久删除隔离文件 ${name} 吗？`))
      await run('quarantine-delete', (signal) =>
        dependencies.workout.deleteQuarantined(name, signal),
      );
    await refresh();
  }
  async function backup(file: File): Promise<void> {
    const inspection = await run('backup-inspect', (signal) =>
      dependencies.maintenance.inspectBackup(file, signal),
    );
    if (!inspection || !context.shell.ask('导入备份会替换当前本地数据，继续吗？')) return;
    const result = await run('backup-import', (signal) =>
      dependencies.maintenance.importBackup(file, signal),
    );
    if (result) dependencies.reload();
  }
  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onOpen: () => {
            context.shell.openModal('data');
            void refresh();
          },
          onClose: () => context.shell.closeModal('data'),
          onAction: (value) => void action(value),
          onPlan: (value, item) => void plan(value, item),
          onMemory: (value, item, fact) => void memory(value, item, fact),
          onSoreness: (value, item) => void soreness(value, item),
          onHealthDelete: (item) =>
            void run('health-delete', (signal) =>
              dependencies.health.deleteImport(String(item.id), signal),
            ).then(refresh),
          onAuditDelete: (item) => {
            const name = String(item.name);
            if (!context.shell.ask(`永久删除孤立文件 ${name} 吗？`)) return;
            const task =
              item.orphan === 'health'
                ? (signal: AbortSignal) => dependencies.health.deleteRawOrphan(name, signal)
                : (signal: AbortSignal) => dependencies.maintenance.deleteHrOrphan(name, signal);
            void run('audit-delete', task).then(refresh);
          },
          onQuarantine: (value, item) => void quarantine(value, item),
          onRecovery: (value, item) => {
            const name = String(item.name);
            if (value === 'download')
              void run('recovery-download', (signal) =>
                dependencies.maintenance.downloadRecoveryPoint(name, signal),
              ).then((result) => {
                if (result) download(result, name);
              });
            else if (context.shell.ask(`删除恢复点 ${name} 吗？`))
              void run('recovery-delete', (signal) =>
                dependencies.maintenance.deleteRecoveryPoint(name, signal),
              ).then(refresh);
          },
          onViewer: (mode, day, explicit) => void viewer(mode, day, explicit),
          onNutrition: (value, item, patch) => {
            if (value === 'save')
              void run('nutrition-save', (signal) =>
                dependencies.records.updateNutrition(
                  String(item.id),
                  Number(item.revision),
                  patch ?? {},
                  signal,
                ),
              ).then(() => viewer('nutrition', state.viewerDay));
            else if (context.shell.ask('确定删除该营养组吗？'))
              void run('nutrition-delete', (signal) =>
                dependencies.records.deleteNutrition(
                  String(item.id),
                  Number(item.revision),
                  signal,
                ),
              ).then(() => viewer('nutrition', state.viewerDay));
          },
          onBackup: (file) => void backup(file),
        }),
        context.events.on('data:refresh', () => {
          if (context.shell.isModalOpen('data')) void refresh();
        }),
        context.events.on(
          'data:view-mode',
          ({ mode, day }) => void viewer(mode, day ?? state.viewerDay),
        ),
      );
      void viewer('training', state.viewerDay);
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      context.operations
        .pending()
        .filter((key) => key.startsWith('data-'))
        .forEach((key) => context.operations.cancel(key));
    },
  };
}
