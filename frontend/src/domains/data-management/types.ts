import type { JsonObject } from '../../shared/types';
export interface DataManagementState {
  overview: JsonObject | null;
  loading: boolean;
  error?: string;
  viewerMode: 'training' | 'nutrition';
  viewerDay: string;
  viewerItems: JsonObject[];
  selectedRecordIds: Set<string>;
  selectedPlanIds: Set<string>;
}
