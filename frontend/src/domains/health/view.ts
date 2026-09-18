import type { AppShell } from '../../app/shell';
import {
  formatHealthDuration,
  formatHealthTime,
  formatHealthValue,
  trendSummary,
  type TrendPoint,
} from '../../shared/health-metrics';
import type { JsonObject } from '../../shared/types';
import type { HealthDomainState } from './types';

export interface HealthViewHandlers {
  onOpen(): void;
  onClose(): void;
  onDate(day: string): void;
  onShift(offset: number): void;
  onTrend(metric: string, period: HealthDomainState['period'], day: string): void;
}

export interface HealthView {
  bind(handlers: HealthViewHandlers): () => void;
  renderOverview(state: HealthDomainState): void;
  renderTrend(state: HealthDomainState): void;
}

function required<T extends Element>(document: Document, selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Health element not found: ${selector}`);
  return element;
}

function record(value: unknown): JsonObject | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : null;
}

function detail(text: unknown, tone?: string): { text: string; tone?: string } {
  return { text: text == null ? '' : String(text), tone };
}

function card(
  document: Document,
  title: string,
  value: string,
  unit: string,
  details: Array<{ text: string; tone?: string }>,
): HTMLElement {
  const root = document.createElement('section');
  root.className = 'overview-card';
  const heading = document.createElement('h3');
  heading.textContent = title;
  const primary = document.createElement('div');
  primary.className = 'overview-value';
  primary.append(document.createTextNode(value));
  if (unit) {
    const suffix = document.createElement('small');
    suffix.textContent = ` ${unit}`;
    primary.append(suffix);
  }
  const meta = document.createElement('div');
  meta.className = 'overview-details';
  details
    .filter((item) => item.text)
    .forEach((item) => {
      const node = document.createElement('span');
      node.textContent = item.text;
      if (item.tone) node.className = `overview-detail-${item.tone}`;
      meta.append(node);
    });
  root.append(heading, primary, meta);
  return root;
}

function overviewCards(document: Document, data: JsonObject): HTMLElement[] {
  const sleep = record(data.sleep);
  const heart = record(data.heart_rate);
  const stress = record(data.stress);
  const hrv = record(data.hrv);
  const activity = record(data.activity);
  const intensity = record(data.intensity);
  const device = record(data.device);
  const respiration = record(data.respiration);
  const spo2 = record(data.spo2);
  const hrvValue = hrv?.last_night_average_ms ?? hrv?.avg;
  return [
    card(
      document,
      '睡眠（按起床日）',
      sleep ? formatHealthDuration(sleep.duration_min) : '--',
      '',
      sleep
        ? [
            detail(sleep.score != null ? `评分 ${sleep.score}` : '未提供睡眠评分'),
            detail(
              sleep.time_in_bed_min != null
                ? `卧床 ${formatHealthDuration(sleep.time_in_bed_min)}（清醒 ${Math.round(Number(sleep.awake_min) || 0)} 分钟）`
                : '',
            ),
            detail(sleep.restlessness != null ? `不安稳 ${sleep.restlessness}` : ''),
            detail(
              sleep.night_avg_hr != null
                ? `夜间心率 ${formatHealthValue(sleep.night_avg_hr, 1)} bpm（最低 ${formatHealthValue(sleep.night_min_hr)}）`
                : '',
            ),
            detail(
              sleep.bed_start_local
                ? `${formatHealthTime(sleep.bed_start_local)} - ${formatHealthTime(sleep.bed_end_local)}`
                : sleep.coverage_start
                  ? `${formatHealthTime(sleep.coverage_start)} - ${formatHealthTime(sleep.coverage_end)}`
                  : '',
            ),
            detail(
              sleep.device_stage_deep_min != null
                ? `手表粗分期：深睡 ${Math.round(Number(sleep.device_stage_deep_min))} / REM ${Math.round(Number(sleep.device_stage_rem_min) || 0)} 分钟（与 Connect 口径不同）`
                : '',
            ),
            detail(sleep.duration_note),
          ]
        : [detail('该日期未导入睡眠数据', 'warn')],
    ),
    card(
      document,
      '恢复与压力',
      hrvValue != null ? formatHealthValue(hrvValue, 1) : '--',
      hrvValue != null ? 'ms 昨夜平均 HRV' : '',
      [
        detail(stress ? `平均压力 ${formatHealthValue(stress.avg, 1)}` : '未导入压力数据'),
        detail(
          hrv?.baseline_balanced_lower_ms != null && hrv.baseline_balanced_upper_ms != null
            ? `平衡区 ${formatHealthValue(hrv.baseline_balanced_lower_ms)}-${formatHealthValue(hrv.baseline_balanced_upper_ms)} ms`
            : '',
        ),
        detail(
          hrv?.weekly_average_ms != null
            ? `七日平均 ${formatHealthValue(hrv.weekly_average_ms, 1)} ms`
            : '',
        ),
        detail(
          hrv?.last_night_5min_high_ms != null
            ? `昨夜 5 分钟峰值 ${formatHealthValue(hrv.last_night_5min_high_ms, 1)} ms`
            : '',
        ),
        detail(hrv?.status ? `状态 ${hrv.status}` : ''),
      ],
    ),
    card(
      document,
      '全天心率',
      heart ? formatHealthValue(heart.avg, 1) : '--',
      heart ? 'bpm 平均' : '',
      heart
        ? [
            detail(`最低 ${formatHealthValue(heart.min)} bpm`),
            detail(`最高 ${formatHealthValue(heart.max)} bpm`),
            detail(
              heart.coverage_start
                ? `覆盖 ${formatHealthTime(heart.coverage_start)} - ${formatHealthTime(heart.coverage_end)}`
                : '',
            ),
          ]
        : [detail('该日期未导入心率数据', 'warn')],
    ),
    card(
      document,
      '活动',
      activity?.steps != null ? formatHealthValue(activity.steps) : '--',
      activity?.steps != null ? '步' : '',
      activity
        ? [
            detail(
              activity.distance_m != null
                ? `距离 ${formatHealthValue(Number(activity.distance_m) / 1000, 2)} km`
                : '',
            ),
            detail(
              activity.active_calories != null
                ? `活动消耗 ${formatHealthValue(activity.active_calories)} kcal`
                : '',
            ),
            detail(
              activity.resting_calories != null
                ? `静态消耗 ${formatHealthValue(activity.resting_calories)} kcal${device?.resting_metabolic_rate != null ? '（手表静息代谢率）' : ''}`
                : '静态消耗：该日期未导入手表静息代谢率',
            ),
            detail(
              activity.total_calories != null
                ? `总消耗 ${formatHealthValue(activity.total_calories)} kcal`
                : '',
            ),
            detail(
              activity.active_time_min != null
                ? `活动 ${formatHealthValue(activity.active_time_min)} 分钟`
                : '',
            ),
          ]
        : [detail('该日期未导入活动数据', 'warn')],
    ),
    card(
      document,
      '强度分钟',
      intensity ? formatHealthValue(intensity.intensity_minutes) : '--',
      intensity ? '分钟（高强度双倍）' : '',
      intensity
        ? [
            detail(
              intensity.moderate_min != null
                ? `中等强度 ${formatHealthValue(intensity.moderate_min)} 分钟`
                : '',
            ),
            detail(
              intensity.vigorous_min != null
                ? `高强度 ${formatHealthValue(intensity.vigorous_min)} 分钟`
                : '',
            ),
            detail('WHO 建议每周 150 分钟'),
          ]
        : [detail('该日期未导入强度分钟数据', 'warn')],
    ),
    card(
      document,
      '静息指标',
      device?.resting_heart_rate != null ? formatHealthValue(device.resting_heart_rate) : '--',
      device?.resting_heart_rate != null ? 'bpm 静息心率' : '',
      device
        ? [
            detail(
              device.resting_heart_rate_baseline != null
                ? `基线 ${formatHealthValue(device.resting_heart_rate_baseline)} bpm`
                : '',
            ),
            detail(
              device.resting_metabolic_rate != null
                ? `静息代谢率 ${formatHealthValue(device.resting_metabolic_rate)} kcal/天`
                : '',
            ),
            detail(
              device.utc_offset_minutes != null
                ? `设备时区 UTC${Number(device.utc_offset_minutes) >= 0 ? '+' : ''}${Number(device.utc_offset_minutes) / 60}`
                : '',
            ),
            detail(device.resting_heart_rate != null ? '静息心率来自手表私有字段，非官方接口' : ''),
          ]
        : [detail('该日期未导入静息指标', 'warn')],
    ),
    card(
      document,
      '血氧饱和度',
      spo2 ? formatHealthValue(spo2.avg, 1) : '--',
      spo2 ? '%' : '',
      spo2
        ? [
            detail(`最低 ${formatHealthValue(spo2.min, 1)}%`),
            detail(`最高 ${formatHealthValue(spo2.max, 1)}%`),
            detail(`采样 ${spo2.samples} 次`),
          ]
        : [detail('该日期未导入血氧数据', 'warn')],
    ),
    card(
      document,
      '呼吸频率',
      respiration ? formatHealthValue(respiration.avg, 1) : '--',
      respiration ? 'brpm 平均' : '',
      respiration
        ? [
            detail(`最低 ${formatHealthValue(respiration.min, 1)}`),
            detail(`最高 ${formatHealthValue(respiration.max, 1)}`),
            detail(`采样 ${respiration.samples} 次`),
          ]
        : [detail('该日期未导入呼吸数据', 'warn')],
    ),
  ];
}

function svg(document: Document, name: string, attrs: Record<string, unknown> = {}): SVGElement {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function renderChart(
  document: Document,
  root: SVGSVGElement,
  points: TrendPoint[],
  metric: string,
  period: string,
): void {
  root.replaceChildren();
  root.setAttribute('viewBox', '0 0 360 180');
  const width = 360;
  const height = 180;
  const left = 34;
  const right = 10;
  const top = 12;
  const bottom = 28;
  if (!points.length) {
    const empty = svg(document, 'text', { x: 180, y: 90, class: 'trend-empty' });
    empty.textContent = `此时间范围暂无${metric}数据`;
    root.append(empty);
    return;
  }
  const values = points.map((point) => point.value);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const padding = rawMin === rawMax ? Math.max(1, rawMin * 0.08) : (rawMax - rawMin) * 0.12;
  const min = Math.max(0, rawMin - padding);
  const max = rawMax + padding;
  const chartWidth = width - left - right;
  const chartHeight = height - top - bottom;
  const x = (index: number) =>
    left + (points.length === 1 ? chartWidth / 2 : (index / (points.length - 1)) * chartWidth);
  const y = (value: number) => top + ((max - value) / (max - min || 1)) * chartHeight;
  for (let index = 0; index < 3; index += 1) {
    const py = top + (index * chartHeight) / 2;
    root.append(
      svg(document, 'line', { x1: left, y1: py, x2: width - right, y2: py, class: 'trend-grid' }),
    );
    const label = svg(document, 'text', {
      x: left - 5,
      y: py + 3,
      class: 'trend-axis',
      'text-anchor': 'end',
    });
    label.textContent = (max - (index * (max - min)) / 2).toFixed(metric === 'spo2' ? 1 : 0);
    root.append(label);
  }
  const positions = points.map((point, index) => [x(index), y(point.value)]);
  const line = positions
    .map((point, index) => `${index ? 'L' : 'M'}${point[0].toFixed(1)} ${point[1].toFixed(1)}`)
    .join(' ');
  const area = `${line} L${positions.at(-1)![0].toFixed(1)} ${height - bottom} L${positions[0][0].toFixed(1)} ${height - bottom} Z`;
  root.append(
    svg(document, 'path', { d: area, class: 'trend-area' }),
    svg(document, 'path', { d: line, class: 'trend-line' }),
  );
  const labels = new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]);
  positions.forEach((position, index) => {
    const dot = svg(document, 'circle', {
      cx: position[0],
      cy: position[1],
      r: 3,
      class: 'trend-point',
    });
    const title = svg(document, 'title');
    title.textContent = `${points[index].label}: ${points[index].value}`;
    dot.append(title);
    root.append(dot);
    if (labels.has(index)) {
      const label = svg(document, 'text', {
        x: position[0],
        y: height - 8,
        class: 'trend-axis',
        'text-anchor': 'middle',
      });
      label.textContent = period === 'day' ? points[index].label : points[index].label.slice(5);
      root.append(label);
    }
  });
}

export function createHealthView(document: Document, shell: AppShell): HealthView {
  const open = required<HTMLButtonElement>(document, '#btn-overview');
  const close = required<HTMLButtonElement>(document, '#health-modal-close');
  const date = required<HTMLInputElement>(document, '#overview-date');
  const previous = required<HTMLButtonElement>(document, '#overview-prev');
  const next = required<HTMLButtonElement>(document, '#overview-next');
  const status = required<HTMLElement>(document, '#overview-status');
  const grid = required<HTMLElement>(document, '#overview-grid');
  const metric = required<HTMLSelectElement>(document, '#trend-metric');
  const trendDate = required<HTMLInputElement>(document, '#trend-date');
  const summary = required<HTMLElement>(document, '#trend-summary');
  const chart = required<SVGSVGElement>(document, '#trend-chart');
  const periodButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('.trend-period'));
  const listeners: Array<[EventTarget, string, EventListener]> = [];
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };
  return {
    bind(handlers) {
      listen(open, 'click', handlers.onOpen);
      listen(close, 'click', handlers.onClose);
      shell.modal('health').onRequestClose = handlers.onClose;
      listen(date, 'change', () => {
        if (date.value) handlers.onDate(date.value);
      });
      listen(previous, 'click', () => handlers.onShift(-1));
      listen(next, 'click', () => handlers.onShift(1));
      shell.registerTabs('trend-period', required(document, '.trend-periods'), periodButtons, {
        ariaLabel: '选择时间范围',
        readValue: (button) => button.dataset.period as HealthDomainState['period'],
        initial: 'day',
        onChange: (value) => handlers.onTrend(metric.value, value, trendDate.value),
      });
      listen(metric, 'change', () =>
        handlers.onTrend(
          metric.value,
          shell.tabs<HealthDomainState['period']>('trend-period')?.current() ?? 'day',
          trendDate.value,
        ),
      );
      listen(trendDate, 'change', () =>
        handlers.onTrend(
          metric.value,
          shell.tabs<HealthDomainState['period']>('trend-period')?.current() ?? 'day',
          trendDate.value,
        ),
      );
      return () =>
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
    },
    renderOverview(state) {
      document.documentElement.dataset.healthDate = state.selectedDate;
      if (state.overviewStatus === 'loading') {
        status.classList.remove('empty');
        status.textContent = '正在加载健康数据…';
        return;
      }
      if (state.overviewStatus === 'error') {
        grid.replaceChildren();
        status.classList.add('empty');
        status.textContent = state.overviewError ?? '健康总览加载失败';
        return;
      }
      if (!state.overview) return;
      date.value = state.selectedDate;
      const sections = Array.isArray(state.overview.available_sections)
        ? state.overview.available_sections
        : [];
      const labels: Record<string, string> = {
        sleep: '睡眠',
        heart_rate: '心率',
        stress: '压力',
        hrv: 'HRV',
        activity: '活动',
        intensity: '强度分钟',
        device: '静息指标',
      };
      status.classList.toggle('empty', !state.overview.has_data);
      status.textContent = state.overview.has_data
        ? `${state.selectedDate} · 已导入 ${sections.map((name) => labels[String(name)] || String(name)).join('、')} 数据`
        : `${state.selectedDate} 暂无已导入的健康数据`;
      grid.replaceChildren(...overviewCards(document, state.overview));
    },
    renderTrend(state) {
      metric.value = state.metric;
      trendDate.value = state.trendDate;
      if (state.trendStatus === 'loading') {
        summary.textContent = '正在加载健康数据…';
        return;
      }
      if (state.trendStatus === 'error') {
        summary.textContent = state.trendError ?? '健康趋势加载失败';
        renderChart(document, chart, [], state.metric, state.period);
        return;
      }
      if (!state.trend) return;
      summary.textContent = trendSummary(state.trend, state.trendPoints);
      renderChart(document, chart, state.trendPoints, state.metric, state.period);
    },
  };
}
