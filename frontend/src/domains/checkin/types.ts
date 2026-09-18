import type { JsonObject } from '../../shared/types';
import type { MealEstimate } from '../../shared/nutrition';

export interface CheckinDomainState {
  current: JsonObject | null;
  day: string;
  meals: MealEstimate[];
  loadedFields: Set<string>;
  status: 'idle' | 'loading' | 'editing' | 'saving' | 'error';
  error?: string;
}
