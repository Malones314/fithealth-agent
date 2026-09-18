import { healthApi } from '../../api/health';
import { isAbort } from '../../shared/operations';
import { shiftIsoDate, validTrendPoints } from '../../shared/health-metrics';
import type { DomainContext, DomainController } from '../domain-factory';
import { createHealthState } from './state';
import { createHealthView } from './view';

export interface HealthDependencies {
  api: typeof healthApi;
}
const defaults: HealthDependencies = { api: healthApi };
const message = (error: unknown) => (error instanceof Error ? error.message : '请求失败');

export function createHealthController(
  context: DomainContext,
  dependencies: HealthDependencies = defaults,
): DomainController {
  const state = createHealthState();
  const view = createHealthView(context.document, context.shell);
  const disposers: Array<() => void> = [];
  let started = false;

  async function overview(day = ''): Promise<void> {
    state.overviewStatus = 'loading';
    state.overviewError = undefined;
    view.renderOverview(state);
    const operation = context.operations.begin('health-overview');
    try {
      const data = await dependencies.api.overview(day, operation.signal);
      if (!operation.isCurrent()) return;
      state.overview = data;
      state.selectedDate = String(data.date ?? day);
      state.overviewStatus = 'ready';
      view.renderOverview(state);
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.overviewStatus = 'error';
        state.overviewError = message(error);
        view.renderOverview(state);
      }
    }
  }

  async function trend(
    metric = state.metric,
    period = state.period,
    day = state.trendDate,
    useDate = true,
  ): Promise<void> {
    state.metric = metric;
    state.period = period;
    state.trendDate = day;
    state.trendStatus = 'loading';
    state.trendError = undefined;
    view.renderTrend(state);
    const params = new URLSearchParams({ metric, period });
    if (useDate && day) params.set('end_date', day);
    const operation = context.operations.begin('health-trend');
    try {
      const data = await dependencies.api.trend(params, operation.signal);
      if (!operation.isCurrent()) return;
      state.trend = data;
      state.metric = String(data.metric ?? metric);
      state.trendDate = String(data.end_date ?? day);
      state.trendPoints = validTrendPoints(data.items);
      state.trendStatus = 'ready';
      view.renderTrend(state);
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.trendStatus = 'error';
        state.trendError = message(error);
        state.trendPoints = [];
        view.renderTrend(state);
      }
    }
  }

  async function requestData<
    K extends
      | 'daily'
      | 'range'
      | 'sleep'
      | 'rawAudit'
      | 'importDetails'
      | 'deleteImport'
      | 'deleteRawOrphan',
  >(kind: K, argument?: string | URLSearchParams): Promise<unknown> {
    const operation = context.operations.begin(`health-${kind}`);
    const api = dependencies.api;
    try {
      let result: unknown;
      if (kind === 'daily') result = await api.daily(String(argument), operation.signal);
      else if (kind === 'range')
        result = await api.range(argument as URLSearchParams, operation.signal);
      else if (kind === 'sleep') result = await api.sleep(String(argument), operation.signal);
      else if (kind === 'rawAudit') result = await api.rawAudit(operation.signal);
      else if (kind === 'importDetails')
        result = await api.importDetails(String(argument), operation.signal);
      else if (kind === 'deleteImport')
        result = await api.deleteImport(String(argument), operation.signal);
      else result = await api.deleteRawOrphan(String(argument), operation.signal);
      return operation.isCurrent() ? result : undefined;
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) context.shell.toast.error(message(error));
      return undefined;
    }
  }

  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onOpen: () => {
            context.shell.openModal('health');
            void overview();
          },
          onClose: () => {
            context.operations.cancel('health-overview');
            context.shell.closeModal('health');
          },
          onDate: (day) => void overview(day),
          onShift: (offset) => {
            if (state.selectedDate) void overview(shiftIsoDate(state.selectedDate, offset));
          },
          onTrend: (metric, period, day) => void trend(metric, period, day),
        }),
        context.events.on('startup', () => void trend(state.metric, state.period, '', false)),
        context.events.on('health:refresh', () => {
          void trend(state.metric, state.period, state.trendDate, false);
          if (context.shell.isModalOpen('health')) void overview(state.selectedDate);
        }),
        context.events.on(
          'health:daily',
          ({ day, resolve }) => void requestData('daily', day).then(resolve),
        ),
        context.events.on(
          'health:range',
          ({ params, resolve }) =>
            void requestData('range', new URLSearchParams(params)).then(resolve),
        ),
        context.events.on(
          'health:sleep',
          ({ day, resolve }) => void requestData('sleep', day).then(resolve),
        ),
        context.events.on(
          'health:raw-audit',
          ({ resolve }) => void requestData('rawAudit').then(resolve),
        ),
        context.events.on(
          'health:import-details',
          ({ id, resolve }) => void requestData('importDetails', id).then(resolve),
        ),
        context.events.on(
          'health:delete-import',
          ({ id, resolve }) => void requestData('deleteImport', id).then(resolve),
        ),
        context.events.on(
          'health:delete-raw-orphan',
          ({ name, resolve }) => void requestData('deleteRawOrphan', name).then(resolve),
        ),
      );
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      for (const key of [
        'overview',
        'trend',
        'daily',
        'range',
        'sleep',
        'rawAudit',
        'importDetails',
        'deleteImport',
        'deleteRawOrphan',
      ])
        context.operations.cancel(`health-${key}`);
    },
  };
}
