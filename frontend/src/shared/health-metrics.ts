import type { JsonObject } from './types';

export function localDateISO(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function shiftIsoDate(day: string, offset: number): string {
  const date = new Date(`${day}T00:00:00`);
  date.setDate(date.getDate() + offset);
  return localDateISO(date);
}

export function formatHealthValue(value: unknown, digits = 0): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

export function formatHealthDuration(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  const minutes = Math.max(0, Math.round(value));
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

export function formatHealthTime(value: unknown): string {
  return typeof value === 'string' && value.length >= 16 ? value.slice(11, 16) : '--';
}

export interface TrendPoint extends JsonObject {
  label: string;
  value: number;
}

export function validTrendPoints(value: unknown): TrendPoint[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const point = item as JsonObject;
    const number = Number(point.value);
    return Number.isFinite(number)
      ? [{ ...point, label: String(point.label ?? ''), value: number } as TrendPoint]
      : [];
  });
}

export function trendSummary(data: JsonObject, points: TrendPoint[]): string {
  const start = String(data.start_date ?? '');
  const end = String(data.end_date ?? '');
  const unit = data.unit ? ` ${String(data.unit)}` : '';
  if (!points.length) return `${start} 至 ${end}`;
  if (data.cumulative) {
    const last = points.at(-1)!;
    return (
      `${end} · 截至 ${last.label} 累计 ${last.value.toFixed(0)}${unit}` +
      `（静息 ${Number(last.resting_calories ?? 0).toFixed(0)}` +
      ` + 活动 ${Number(last.active_calories ?? 0).toFixed(0)}，静息按日速率均摊）`
    );
  }
  const average = points.reduce((sum, point) => sum + point.value, 0) / points.length;
  return `${start} 至 ${end} · 平均 ${average.toFixed(1)}${unit} · ${points.length} 个数据点`;
}
