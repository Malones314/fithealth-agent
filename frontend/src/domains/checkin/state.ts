import type { CheckinDomainState } from './types';
export function createCheckinState(): CheckinDomainState {
  return { current: null, day: '', meals: [], loadedFields: new Set(), status: 'idle' };
}
