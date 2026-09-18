import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createDataManagementController, type DataManagementDependencies } from './controller';
const ids = [
  'btn-data',
  'data-modal-close',
  'delete-records',
  'delete-plans',
  'clear-memories',
  'delete-pending',
  'reset-profile',
  'reset-all',
  'export-backup',
  'import-backup',
  'add-soreness',
  'view-record',
  'record-preview-close',
  'plan-preview-close',
  'plan-preview-download',
  'viewer-training',
  'viewer-nutrition',
  'records-list',
  'records-count',
  'plans-list',
  'plans-count',
  'memories-list',
  'memories-count',
  'daily-records-list',
  'daily-records-count',
  'soreness-list',
  'soreness-count',
  'health-imports-list',
  'health-imports-count',
  'profile-data-summary',
  'data-audit-list',
  'data-audit-count',
  'quarantined-list',
  'quarantined-count',
  'recovery-points-list',
  'recovery-points-count',
  'data-status',
  'record-preview',
  'record-preview-title',
  'record-preview-body',
  'plan-preview',
  'plan-preview-title',
  'plan-preview-body',
  'viewer-content-title',
];
function mount() {
  document.body.innerHTML = `<div id="chat"></div><div id="data-modal"><div role="dialog"></div></div><div class="data-viewer-switch"></div><select id="viewer-record-select"></select><input id="viewer-date"><div id="viewer-detail"></div><input id="backup-file" type="file"><input id="reset-confirmation">${ids.map((id) => (id.startsWith('btn-') || ['delete-records', 'delete-plans', 'clear-memories', 'delete-pending', 'reset-profile', 'reset-all', 'export-backup', 'import-backup', 'add-soreness', 'view-record', 'data-modal-close', 'record-preview-close', 'plan-preview-close', 'plan-preview-download', 'viewer-training', 'viewer-nutrition'].includes(id) ? `<button id="${id}"></button>` : `<div id="${id}"></div>`)).join('')}`;
  document
    .querySelector('.data-viewer-switch')!
    .append(
      document.querySelector('#viewer-training')!,
      document.querySelector('#viewer-nutrition')!,
    );
}
function context(): DomainContext {
  return {
    state: createAppState(),
    events: createEventBus(),
    shell: { ...createShell(), ask: () => true },
    operations: createOperationRegistry(),
    document,
  };
}
function deps(): DataManagementDependencies {
  const empty = {
    records: [],
    plans: [],
    memories: [],
    daily_records: [],
    soreness_reports: [],
    health_imports: [],
    profile_complete: true,
  };
  return {
    maintenance: {
      overview: vi.fn().mockResolvedValue(empty),
      recoveryPoints: vi.fn().mockResolvedValue({ points: [] }),
      hrAudit: vi.fn().mockResolvedValue({ missing: [], orphans: [] }),
      exportBackup: vi.fn(),
      inspectBackup: vi.fn(),
      importBackup: vi.fn(),
      deleteRecoveryPoint: vi.fn(),
      downloadRecoveryPoint: vi.fn(),
      deletePendingWorkout: vi.fn(),
      resetProfile: vi.fn(),
      reset: vi.fn(),
      retryReset: vi.fn(),
      deleteHrOrphan: vi.fn(),
    },
    memories: {
      confirmFact: vi.fn(),
      rejectFact: vi.fn(),
      updateFact: vi.fn(),
      rollbackFact: vi.fn(),
      confirm: vi.fn(),
      remove: vi.fn(),
      clear: vi.fn(),
      addSoreness: vi.fn(),
      updateSoreness: vi.fn(),
      removeSoreness: vi.fn(),
    },
    plans: {
      get: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
      removeBatch: vi.fn(),
    },
    records: {
      training: vi.fn().mockResolvedValue({ items: [] }),
      nutrition: vi.fn().mockResolvedValue({ items: [] }),
      trainingRecord: vi.fn(),
      deleteBatch: vi.fn(),
      deleteTraining: vi.fn(),
      updateNutrition: vi.fn(),
      deleteNutrition: vi.fn(),
    },
    workout: {
      quarantined: vi.fn().mockResolvedValue({ files: [] }),
      preview: vi.fn(),
      restoreQuarantined: vi.fn(),
      dismissQuarantined: vi.fn(),
      deleteQuarantined: vi.fn(),
    },
    health: {
      rawAudit: vi.fn().mockResolvedValue({ missing: [], orphans: [] }),
      deleteImport: vi.fn(),
      deleteRawOrphan: vi.fn(),
    },
    prompt: vi.fn(),
    reload: vi.fn(),
  } as unknown as DataManagementDependencies;
}
beforeEach(mount);
describe('data management domain', () => {
  it('loads overview, audits, quarantine and recovery through adapters', async () => {
    const ctx = context();
    const api = deps();
    const controller = createDataManagementController(ctx, api);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-data')!.click();
    await vi.waitFor(() =>
      expect(document.querySelector('#data-audit-list')?.textContent).toContain(
        '文件与数据库引用一致',
      ),
    );
    expect(api.maintenance.overview).toHaveBeenCalled();
    expect(api.workout.quarantined).toHaveBeenCalledWith(true, expect.any(AbortSignal));
    controller.stop?.();
  });
  it('addresses memory facts by stable fact_id and reports skipped schedule keys', async () => {
    const ctx = context();
    const api = deps();
    api.maintenance.overview = vi.fn().mockResolvedValue({
      records: [],
      plans: [],
      daily_records: [],
      soreness_reports: [],
      health_imports: [],
      profile_complete: true,
      memories: [
        {
          id: 'm1',
          summary: '记忆',
          facts: [
            {
              fact_id: 'stable-9',
              key: 'weekly_schedule',
              value: '{"days":{"mon":{"type":"training"}},"invalid_days":["周二"]}',
            },
          ],
        },
      ],
    });
    api.memories.confirmFact = vi.fn().mockResolvedValue({});
    const controller = createDataManagementController(ctx, api);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-data')!.click();
    await vi.waitFor(() =>
      expect(document.querySelector('#memories-list')?.textContent).toContain(
        '已识别 1 天，跳过：周二',
      ),
    );
    document.querySelector<HTMLButtonElement>('#memories-list button')!.click();
    await vi.waitFor(() => expect(api.memories.confirmFact).toHaveBeenCalled());
    expect(api.memories.confirmFact).toHaveBeenCalledWith(
      'm1',
      'stable-9',
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });
  it('offers quarantine dismissal and refreshes through the typed adapter', async () => {
    const ctx = context();
    const api = deps();
    api.workout.quarantined = vi.fn().mockResolvedValue({
      files: [{ name: 'pending.corrupt.json', recoverable: true, dismissed: false }],
    });
    api.workout.dismissQuarantined = vi.fn().mockResolvedValue({});
    const controller = createDataManagementController(ctx, api);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-data')!.click();
    await vi.waitFor(() =>
      expect(document.querySelector('#quarantined-list')?.textContent).toContain('不再提醒'),
    );
    expect(document.querySelector('#quarantined-list')?.textContent).toContain('永久删除');
    Array.from(document.querySelectorAll<HTMLButtonElement>('#quarantined-list button'))
      .find((button) => button.textContent === '不再提醒')!
      .click();
    await vi.waitFor(() =>
      expect(api.workout.dismissQuarantined).toHaveBeenCalledWith(
        'pending.corrupt.json',
        expect.any(AbortSignal),
      ),
    );
    controller.stop?.();
  });
  it('edits complete nutrition values through the records adapter', async () => {
    const ctx = context();
    const api = deps();
    api.records.nutrition = vi.fn().mockResolvedValue({
      items: [
        {
          id: 'meal-1',
          revision: 4,
          kind: 'manual',
          name: '晚餐',
          calories_kcal: 500,
          protein_g: 30,
          carbs_g: 60,
          fat_g: 15,
          note: '旧备注',
        },
      ],
    });
    api.records.updateNutrition = vi.fn().mockResolvedValue({});
    const controller = createDataManagementController(ctx, api);
    controller.start();
    document.querySelector<HTMLButtonElement>('#viewer-nutrition')!.click();
    await vi.waitFor(() =>
      expect(
        document.querySelector<HTMLInputElement>('#viewer-detail [name="calories_kcal"]')?.value,
      ).toBe('500'),
    );
    document.querySelector<HTMLInputElement>('#viewer-detail [name="protein_g"]')!.value = '35';
    document.querySelector<HTMLTextAreaElement>('#viewer-detail [name="note"]')!.value = '新备注';
    Array.from(document.querySelectorAll<HTMLButtonElement>('#viewer-detail button'))
      .find((button) => button.textContent === '保存营养组')!
      .click();
    await vi.waitFor(() => expect(api.records.updateNutrition).toHaveBeenCalled());
    expect(api.records.updateNutrition).toHaveBeenCalledWith(
      'meal-1',
      4,
      expect.objectContaining({ calories_kcal: 500, protein_g: 35, note: '新备注' }),
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });
  it('retries only failed reset steps', async () => {
    const ctx = context();
    const api = deps();
    api.maintenance.reset = vi.fn().mockResolvedValue({
      partial: true,
      steps: [{ key: 'health', label: '健康数据', error: 'busy' }],
    });
    api.maintenance.retryReset = vi.fn().mockResolvedValue({ partial: false, steps: [] });
    const controller = createDataManagementController(ctx, api);
    controller.start();
    document.querySelector<HTMLInputElement>('#reset-confirmation')!.value = '删除全部数据';
    document.querySelector<HTMLButtonElement>('#reset-all')!.click();
    await vi.waitFor(() =>
      expect(document.querySelector('#data-status')?.textContent).toContain('重试失败项目'),
    );
    Array.from(document.querySelectorAll<HTMLButtonElement>('#data-status button'))
      .find((button) => button.textContent === '重试失败项目')!
      .click();
    await vi.waitFor(() =>
      expect(api.maintenance.retryReset).toHaveBeenCalledWith(['health'], expect.any(AbortSignal)),
    );
    controller.stop?.();
  });
  it('cancels all owned operations on stop', () => {
    const ctx = context();
    const api = deps();
    api.records.training = vi.fn().mockReturnValue(new Promise(() => undefined));
    const controller = createDataManagementController(ctx, api);
    controller.start();
    controller.stop?.();
    expect(ctx.operations.pending()).toEqual([]);
  });
});
