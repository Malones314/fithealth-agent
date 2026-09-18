import { apiClient, type RequestOptions } from './client';
import type { ChatMessage, JsonObject } from '../shared/types';

export interface ChatRequest {
  message: string;
  history?: ChatMessage[];
  pending_memory_entry_ids?: string[];
  source?: string;
  plan_context?: JsonObject | null;
  garmin_recovery_hours?: number;
  soreness_prompt_regions?: string[];
}

export interface ChatResponse extends JsonObject {
  reply?: string;
  artifact?: JsonObject | null;
}

// A chat turn includes multiple model calls, video searches and plan validation.
export const DEFAULT_CHAT_TIMEOUT_MS = 10 * 60_000;
let chatTimeoutMs = DEFAULT_CHAT_TIMEOUT_MS;

export function configureChatTimeout(seconds: number): void {
  if (Number.isInteger(seconds) && seconds >= 30 && seconds <= 3600) {
    chatTimeoutMs = seconds * 1000;
  }
}

export const chatApi = {
  send(
    input: ChatRequest,
    options?: Pick<RequestOptions, 'signal' | 'timeoutMs'>,
  ): Promise<ChatResponse> {
    return apiClient.request('/chat', {
      method: 'POST',
      body: input,
      ...options,
      timeoutMs: options?.timeoutMs ?? chatTimeoutMs,
    });
  },
};
