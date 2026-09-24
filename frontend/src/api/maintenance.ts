import { apiClient } from './client';
import type { DownloadResult } from './client';
import type { JsonObject } from '../shared/types';

export const maintenanceApi = {
  overview(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/overview', { signal });
  },
  recoveryPoints(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/recovery-points', { signal });
  },
  exportBackup(signal?: AbortSignal): Promise<DownloadResult> {
    return apiClient.download('/data/backup/export', { signal });
  },
  inspectBackup(file: File, signal?: AbortSignal): Promise<JsonObject> {
    const body = new FormData();
    body.append('file', file, file.name);
    return apiClient.request('/data/backup/inspect', { method: 'POST', body, signal });
  },
  importBackup(file: File, signal?: AbortSignal): Promise<JsonObject> {
    const body = new FormData();
    body.append('file', file, file.name);
    body.append('confirm_restore', 'true');
    return apiClient.request('/data/backup/import', { method: 'POST', body, signal });
  },
  reset(confirm: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/reset', {
      method: 'POST',
      body: { confirmation: confirm },
      signal,
    });
  },
  retryReset(keys: string[], signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/reset/retry', {
      method: 'POST',
      body: { keys, confirmation: '重试删除所选数据' },
      signal,
    });
  },
  deleteRecoveryPoint(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/recovery-points/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      signal,
    });
  },
  downloadRecoveryPoint(name: string, signal?: AbortSignal): Promise<DownloadResult> {
    return apiClient.download(`/data/recovery-points/${encodeURIComponent(name)}`, { signal });
  },
  deletePendingWorkout(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/pending-workout', { method: 'DELETE', signal });
  },
  resetProfile(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/profile/reset', { method: 'POST', body: {}, signal });
  },
  hrAudit(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/hr-streams/audit', { signal });
  },
  deleteHrOrphan(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/hr-streams/orphans/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      signal,
    });
  },
};
