import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatApi } from './chat';
import { apiClient } from './client';
import { healthApi } from './health';
import { maintenanceApi } from './maintenance';
import { memoriesApi } from './memories';
import { plansApi } from './plans';
import { recordsApi } from './records';
import { sessionApi } from './session';
import { settingsApi } from './settings';
import { uploadsApi } from './uploads';
import { workoutApi } from './workout';

describe('API adapter call contracts', () => {
  it('includes the renewed confirmation for a selective reset retry', async () => {
    await maintenanceApi.retryReset(['records_removed']);
    expect(apiClient.request).toHaveBeenCalledWith(
      '/data/reset/retry',
      expect.objectContaining({
        body: { keys: ['records_removed'], confirmation: '重试删除所选数据' },
      }),
    );
  });

  beforeEach(() => {
    vi.spyOn(apiClient, 'request').mockResolvedValue({});
    vi.spyOn(apiClient, 'download').mockResolvedValue({
      blob: new Blob(),
      filename: 'backup.zip',
      contentType: 'application/zip',
    });
  });

  it('routes every adapter operation through the shared client', async () => {
    const file = new File(['x'], 'input.zip');
    const day = '2026-09-11';
    const params = new URLSearchParams({ start: day });

    await Promise.all([
      chatApi.send({ message: 'hello' }),
      healthApi.overview(day),
      healthApi.daily(day),
      healthApi.trend(params),
      healthApi.range(params),
      healthApi.sleep(day),
      healthApi.rawAudit(),
      healthApi.importDetails('id'),
      healthApi.deleteImport('id'),
      healthApi.deleteRawOrphan('file.fit'),
      healthApi.storageStatus(),
      maintenanceApi.overview(),
      maintenanceApi.recoveryPoints(),
      maintenanceApi.exportBackup(),
      maintenanceApi.inspectBackup(file),
      maintenanceApi.importBackup(file),
      maintenanceApi.reset('confirm'),
      maintenanceApi.retryReset(['records']),
      maintenanceApi.deleteRecoveryPoint('point.zip'),
      maintenanceApi.downloadRecoveryPoint('point.zip'),
      maintenanceApi.deletePendingWorkout(),
      maintenanceApi.resetProfile(),
      maintenanceApi.hrAudit(),
      maintenanceApi.deleteHrOrphan('stream.json'),
      memoriesApi.remove('id'),
      memoriesApi.confirm('id'),
      memoriesApi.confirmFact('id', 'fact'),
      memoriesApi.rejectFact('id', 'fact'),
      memoriesApi.forget({ id: 'fact' }),
      memoriesApi.updateFact('id', 'fact', { value: 'new' }),
      memoriesApi.rollbackFact('id', 'fact'),
      memoriesApi.clear(),
      memoriesApi.addSoreness({ region: 'arm' }),
      memoriesApi.updateSoreness('id', { level: 'sore' }),
      memoriesApi.removeSoreness('id'),
      plansApi.get('id'),
      plansApi.create({ id: 'id', content: 'plan' }),
      plansApi.update('id', {}),
      plansApi.remove('id'),
      plansApi.removeBatch(['id']),
      recordsApi.training(day),
      recordsApi.updateTraining('id', 1, {}),
      recordsApi.nutrition(day),
      recordsApi.checkin(day),
      recordsApi.saveCheckin({ date: day }),
      recordsApi.overview(),
      recordsApi.deleteTraining('id'),
      recordsApi.deleteBatch(['id']),
      recordsApi.trainingRecord('id'),
      recordsApi.updateNutrition('id', 1, {}),
      recordsApi.deleteNutrition('id', 1),
      sessionApi.intro(0),
      sessionApi.logout([]),
      settingsApi.externalModels(),
      settingsApi.updateExternalModels(true),
      settingsApi.runtime(),
      settingsApi.updateRuntime({ chat_timeout_seconds: 600 }),
      settingsApi.testLlm(),
      settingsApi.profileStatus(),
      settingsApi.confirmProfileUpdate({ height_cm: 180 }),
      uploadsApi.fit(file),
      uploadsApi.plan(file),
      uploadsApi.health(file),
      uploadsApi.healthBatch([file]),
      uploadsApi.healthActivity(file, 'input.zip', 'activity.fit'),
      uploadsApi.food(file),
      workoutApi.state(),
      workoutApi.update('discard', {}),
      workoutApi.quarantined(),
      workoutApi.preview('file.fit'),
      workoutApi.restoreQuarantined('file.fit', true),
      workoutApi.dismissQuarantined('file.fit'),
      workoutApi.deleteQuarantined('file.fit'),
      workoutApi.savedRecord('record/id'),
      workoutApi.updateSavedRecord('record/id', 2, { updates: [] }),
      workoutApi.save({ sets: [] }),
    ]);

    expect(apiClient.request).toHaveBeenCalledTimes(74);
    expect(apiClient.download).toHaveBeenCalledTimes(2);
    expect(vi.mocked(apiClient.request).mock.calls.every(([path]) => path.startsWith('/'))).toBe(
      true,
    );
  });
});
