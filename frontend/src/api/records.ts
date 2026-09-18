import { apiClient, HttpApiError } from './client';
import type { JsonObject, TrainingRecord } from '../shared/types';

export const recordsApi = {
  training(day?: string, signal?: AbortSignal): Promise<{ items: TrainingRecord[] }> {
    return apiClient.request(
      `/data/training-records${day ? `?day=${encodeURIComponent(day)}` : ''}`,
      { signal },
    );
  },
  updateTraining(
    id: string,
    revision: number,
    record: JsonObject,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    return apiClient.request(`/data/training-records/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: { revision, record },
      signal,
    });
  },
  nutrition(day?: string, signal?: AbortSignal): Promise<{ items: JsonObject[] }> {
    return apiClient.request(
      `/data/nutrition-records${day ? `?day=${encodeURIComponent(day)}` : ''}`,
      { signal },
    );
  },
  checkin(day: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/checkins/${encodeURIComponent(day)}`, { signal });
  },
  saveCheckin(checkin: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/checkins', { method: 'POST', body: checkin, signal });
  },
  overview(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/overview', { signal });
  },
  deleteTraining(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/records/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      signal,
    });
  },
  deleteBatch(ids: string[], signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/records/delete-batch', {
      method: 'POST',
      body: { ids },
      signal,
    });
  },
  trainingRecord(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/training-records/${encodeURIComponent(id)}`, { signal });
  },
  updateNutrition(
    id: string,
    revision: number,
    patch: JsonObject,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    return apiClient.request(`/data/nutrition-records/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: { revision, ...patch },
      signal,
    });
  },
  deleteNutrition(id: string, revision: number, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/nutrition-records/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      body: { revision },
      signal,
    });
  },
};

export function checkinFieldErrors(error: unknown): Record<string, string> {
  const structural =
    error && typeof error === 'object' ? (error as { status?: unknown; details?: unknown }) : {};
  const status = error instanceof HttpApiError ? error.status : structural.status;
  if (status !== 422) return {};
  const details = error instanceof HttpApiError ? error.details : structural.details;
  if (!details || typeof details !== 'object' || Array.isArray(details)) return {};
  const source =
    'field_errors' in details && details.field_errors && typeof details.field_errors === 'object'
      ? (details.field_errors as Record<string, unknown>)
      : (details as Record<string, unknown>);
  return Object.fromEntries(
    Object.entries(source).filter(([, value]) => typeof value === 'string'),
  ) as Record<string, string>;
}
