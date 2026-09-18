import { apiClient } from './client';
import type { UploadResult } from '../shared/types';

function fileBody(file: File, field = 'file', extra: Record<string, string> = {}): FormData {
  const body = new FormData();
  body.append(field, file, file.name);
  Object.entries(extra).forEach(([key, value]) => body.append(key, value));
  return body;
}

export const uploadsApi = {
  fit(file: File, overwritePending = false, signal?: AbortSignal): Promise<UploadResult> {
    return apiClient.request('/upload_fit', {
      method: 'POST',
      body: fileBody(file, 'file', overwritePending ? { overwrite_pending: 'true' } : {}),
      signal,
    });
  },
  plan(file: File, confirmLarge = false, signal?: AbortSignal): Promise<UploadResult> {
    return apiClient.request('/upload_plan', {
      method: 'POST',
      body: fileBody(file, 'file', { confirm_large: String(confirmLarge) }),
      signal,
    });
  },
  health(file: File, signal?: AbortSignal): Promise<UploadResult> {
    return apiClient.request('/upload_health', { method: 'POST', body: fileBody(file), signal });
  },
  healthBatch(files: File[], signal?: AbortSignal): Promise<UploadResult> {
    const body = new FormData();
    files.forEach((file) => body.append('files', file, file.name));
    return apiClient.request('/upload_health', { method: 'POST', body, signal });
  },
  healthActivity(
    file: File,
    zip: string,
    activity: string,
    signal?: AbortSignal,
  ): Promise<UploadResult> {
    return apiClient.request('/upload_health/activity', {
      method: 'POST',
      body: fileBody(file, 'file', { zip, activity }),
      signal,
    });
  },
  food(file: File, signal?: AbortSignal): Promise<UploadResult> {
    return apiClient.request('/analyze_food', {
      method: 'POST',
      body: fileBody(file, 'image'),
      signal,
    });
  },
};
