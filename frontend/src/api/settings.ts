import { apiClient } from './client';
import type { JsonObject } from '../shared/types';

export const settingsApi = {
  externalModels(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/settings/external-models', { signal });
  },
  updateExternalModels(enabled: boolean, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/settings/external-models', {
      method: 'PUT',
      body: { external_models_enabled: enabled },
      signal,
    });
  },
  runtime(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/settings/runtime', { signal });
  },
  updateRuntime(values: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/settings/runtime', { method: 'PUT', body: values, signal });
  },
  testLlm(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/settings/llm-connectivity', { method: 'POST', body: {}, signal });
  },
  profileStatus(signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/profile/status', { signal });
  },
  confirmProfileUpdate(candidate: JsonObject, signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/profile/confirm-update', {
      method: 'POST',
      body: candidate,
      signal,
    });
  },
};
