import { apiClient, HttpApiError } from './client';
import type { JsonObject, Workout } from '../shared/types';

export const workoutApi = {
  state(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/workout_state', { signal });
  },
  update(action: string, payload: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/workout_state/update', {
      method: 'POST',
      body: { action, ...payload },
      signal,
    });
  },
  quarantined(includeDismissed = false, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(
      `/workout_state/quarantined?include_dismissed=${String(includeDismissed)}`,
      { signal },
    );
  },
  preview(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/workout_state/quarantined/${encodeURIComponent(name)}/preview`, {
      signal,
    });
  },
  restoreQuarantined(
    name: string,
    overwritePending: boolean,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    return apiClient.request('/workout_state/quarantined/restore', {
      method: 'POST',
      body: { name, overwrite_pending: overwritePending },
      signal,
    });
  },
  dismissQuarantined(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/workout_state/quarantined/dismiss', {
      method: 'POST',
      body: { name },
      signal,
    });
  },
  deleteQuarantined(name: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/workout_state/quarantined/delete', {
      method: 'POST',
      body: { name },
      signal,
    });
  },
  savedRecord(recordId: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/training-records/${encodeURIComponent(recordId)}`, {
      signal,
    });
  },
  save(record: Workout, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/workout_state/update', {
      method: 'POST',
      body: { action: 'save', ...record },
      signal,
    });
  },
  updateSavedRecord(
    recordId: string,
    revision: number,
    patch: JsonObject,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    return apiClient.request(`/data/training-records/${encodeURIComponent(recordId)}`, {
      method: 'PATCH',
      body: { ...patch, revision },
      signal,
    });
  },
};

export function isWorkoutConflict(error: unknown): boolean {
  return (
    (error instanceof HttpApiError && error.status === 409) ||
    (typeof error === 'object' && error !== null && 'status' in error && error.status === 409)
  );
}
