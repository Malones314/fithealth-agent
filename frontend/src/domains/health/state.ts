import type { HealthDomainState } from './types';

export function createHealthState(selectedDate = ''): HealthDomainState {
  return {
    selectedDate,
    overview: null,
    overviewStatus: 'idle',
    metric: 'heart_rate',
    period: 'day',
    trendDate: '',
    trend: null,
    trendPoints: [],
    trendStatus: 'idle',
  };
}
