import { apiClient } from './client';
import type { JsonObject } from '../shared/types';

export const memoriesApi = {
  remove(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/memories/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      signal,
    });
  },
  confirm(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/memories/${encodeURIComponent(id)}/confirm`, {
      method: 'POST',
      body: {},
      signal,
    });
  },
  confirmFact(id: string, factRef: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(
      `/data/memories/${encodeURIComponent(id)}/facts/${encodeURIComponent(factRef)}/confirm`,
      { method: 'POST', body: {}, signal },
    );
  },
  rejectFact(id: string, factRef: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(
      `/data/memories/${encodeURIComponent(id)}/facts/${encodeURIComponent(factRef)}/reject`,
      { method: 'POST', body: {}, signal },
    );
  },
  forget(input: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/memories/forget', { method: 'POST', body: input, signal });
  },
  updateFact(
    id: string,
    factRef: string,
    patch: JsonObject,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    return apiClient.request(
      `/data/memories/${encodeURIComponent(id)}/facts/${encodeURIComponent(factRef)}`,
      { method: 'PATCH', body: patch, signal },
    );
  },
  rollbackFact(id: string, factRef: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(
      `/data/memories/${encodeURIComponent(id)}/facts/${encodeURIComponent(factRef)}/rollback`,
      { method: 'POST', body: {}, signal },
    );
  },
  clear(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/memories', { method: 'DELETE', signal });
  },
  addSoreness(input: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/data/soreness', { method: 'POST', body: input, signal });
  },
  updateSoreness(id: string, patch: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/soreness/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: patch,
      signal,
    });
  },
  removeSoreness(id: string, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request(`/data/soreness/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      signal,
    });
  },
};
