import { apiClient } from './client';
import type { HealthOverview, JsonObject } from '../shared/types';
import { requireIsoDate } from '../shared/validation';

export const healthApi = {
  overview(day = '', signal?: AbortSignal): Promise<HealthOverview> {
    return apiClient.request(
      `/health/overview${day ? `?day=${encodeURIComponent(requireIsoDate(day))}` : ''}`,
      {
        signal,
      },
    );
  },
  daily(day: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/daily/${encodeURIComponent(requireIsoDate(day))}`, {
      signal,
    });
  },
  trend(params: URLSearchParams, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/trend?${params.toString()}`, { signal });
  },
  range(params: URLSearchParams, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/range?${params.toString()}`, { signal });
  },
  sleep(day: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/sleep/${encodeURIComponent(requireIsoDate(day))}`, {
      signal,
    });
  },
  rawAudit(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/health/imports/raw-audit', { signal });
  },
  importDetails(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/imports/${encodeURIComponent(id)}`, { signal });
  },
  deleteImport(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/imports/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      signal,
    });
  },
  deleteRawOrphan(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/health/imports/raw-orphans/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      signal,
    });
  },
  storageStatus(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/health/storage-status', { signal });
  },
};
