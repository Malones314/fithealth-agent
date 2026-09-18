import type { LogoutMessage } from '../../api/session';

export type SessionStatus = 'idle' | 'loading' | 'ready' | 'ending' | 'ended' | 'error';

export interface SessionState {
  status: SessionStatus;
  message?: string;
  ended: boolean;
  externalModelsEnabled: boolean;
  garminRecoveryHours: number;
  sorenessPromptRegions: string[];
  conversationHistory: LogoutMessage[];
}

export interface ExternalModelDisclosure {
  name?: string;
  data?: string;
}

export interface ExternalModelSettings {
  external_models_enabled?: boolean;
  disclosure?: ExternalModelDisclosure[];
  local_features?: string[];
}

export interface RuntimeSettings {
  agent_max_steps: number;
  llm_temperature: number;
  llm_max_tokens: number | null;
  llm_timeout_seconds: number;
  llm_max_retries: number;
  chat_timeout_seconds: number;
}
