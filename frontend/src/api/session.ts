import { apiClient } from './client';
import type { JsonObject } from '../shared/types';

export interface SessionIntroResponse extends JsonObject {
  message?: string;
  garmin_recovery_hours?: number;
  soreness_prompt_regions?: string[];
}

export interface LogoutMessage {
  role: 'user' | 'assistant';
  text: string;
}

export const sessionApi = {
  intro(garminRecoveryHours = 0, signal?: AbortSignal): Promise<SessionIntroResponse> {
    return apiClient.request(
      `/session/intro?garmin_recovery_hours=${encodeURIComponent(garminRecoveryHours)}`,
      { signal },
    );
  },
  logout(messages: LogoutMessage[], signal?: AbortSignal): Promise<JsonObject> {
    return apiClient.request('/logout', { method: 'POST', body: { messages }, signal });
  },
};
