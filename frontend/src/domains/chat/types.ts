import type { JsonObject } from '../../shared/types';
export interface ChatDomainState {
  pending: boolean;
  error?: string;
  pendingMemoryEntryIds: string[];
  source: string;
  planContext: JsonObject | null;
}
