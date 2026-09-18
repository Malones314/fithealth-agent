import type { LogoutMessage } from '../../api/session';
import type { SessionState } from './types';

const MAX_HISTORY_ITEMS = 20;
type SessionListener = (state: SessionState) => void;
const listeners = new WeakMap<SessionState, Set<SessionListener>>();

export function createSessionState(): SessionState {
  return {
    status: 'idle',
    ended: false,
    externalModelsEnabled: true,
    garminRecoveryHours: 0,
    sorenessPromptRegions: [],
    conversationHistory: [],
  };
}

export const sessionState = createSessionState();

export function updateSessionState(state: SessionState, patch: Partial<SessionState>): void {
  Object.assign(state, patch);
  listeners.get(state)?.forEach((listener) => listener(state));
}

export function subscribeSessionState(state: SessionState, listener: SessionListener): () => void {
  const stateListeners = listeners.get(state) ?? new Set<SessionListener>();
  stateListeners.add(listener);
  listeners.set(state, stateListeners);
  return () => stateListeners.delete(listener);
}

export function rememberConversation(
  state: SessionState,
  role: LogoutMessage['role'],
  text: string,
): void {
  const content = text.trim();
  if (!content) return;
  state.conversationHistory.push({ role, text: content });
  if (state.conversationHistory.length > MAX_HISTORY_ITEMS) {
    state.conversationHistory.splice(0, state.conversationHistory.length - MAX_HISTORY_ITEMS);
  }
}
