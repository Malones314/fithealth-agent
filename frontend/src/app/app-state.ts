export interface AppState {
  startedAt: number;
  session: 'unknown' | 'ready' | 'maintenance' | 'ended';
  noticeCount: number;
  sessionContext: {
    ended: boolean;
    externalModelsEnabled: boolean;
    garminRecoveryHours: number;
    sorenessPromptRegions: string[];
    conversationHistory: Array<{ role: 'user' | 'assistant'; text: string }>;
  };
}

export function createAppState(
  sessionContext: AppState['sessionContext'] = {
    ended: false,
    externalModelsEnabled: true,
    garminRecoveryHours: 0,
    sorenessPromptRegions: [],
    conversationHistory: [],
  },
): AppState {
  return { startedAt: Date.now(), session: 'unknown', noticeCount: 0, sessionContext };
}
