import type { JsonObject } from '../../shared/types';
import type { TrendPoint } from '../../shared/health-metrics';

export type HealthStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface HealthDomainState {
  selectedDate: string;
  overview: JsonObject | null;
  overviewStatus: HealthStatus;
  overviewError?: string;
  metric: string;
  period: 'day' | 'week' | 'month';
  trendDate: string;
  trend: JsonObject | null;
  trendPoints: TrendPoint[];
  trendStatus: HealthStatus;
  trendError?: string;
}
