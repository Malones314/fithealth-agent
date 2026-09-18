import { apiClient } from './client';
import type { JsonObject, Plan } from '../shared/types';

export const plansApi = {
  get(id: string, signal?: AbortSignal): Promise<Plan> {
    return apiClient.request(`/plans/${encodeURIComponent(id)}`, { signal });
  },
  create(plan: Plan, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/plans', { method: 'POST', body: plan, signal });
  },
  update(id: string, patch: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/plans/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: patch,
      signal,
    });
  },
  remove(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/plans/${encodeURIComponent(id)}`, { method: 'DELETE', signal });
  },
  removeBatch(ids: string[], signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/plans/delete-batch', { method: 'POST', body: { ids }, signal });
  },
};
