import type { ChatDomainState } from './types';
export function createChatState(): ChatDomainState {
  return { pending: false, pendingMemoryEntryIds: [], source: 'chat', planContext: null };
}
