import { localDateISO } from '../../shared/health-metrics';
import type { DataManagementState } from './types';
export function createDataManagementState(): DataManagementState {
  return {
    overview: null,
    loading: false,
    viewerMode: 'training',
    viewerDay: localDateISO(),
    viewerItems: [],
    selectedRecordIds: new Set(),
    selectedPlanIds: new Set(),
  };
}
