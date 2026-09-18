import { checkinFieldErrors, recordsApi } from '../../api/records';
import { localDateISO } from '../../shared/health-metrics';
import {
  normalizeMealEstimate,
  validateMealTotals,
  type MealEstimate,
} from '../../shared/nutrition';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { DomainContext, DomainController } from '../domain-factory';
import { createCheckinState } from './state';
import { createCheckinView } from './view';

export interface CheckinDependencies {
  api: typeof recordsApi;
  prompt: (message: string, initial: string) => string | null;
}
const defaults: CheckinDependencies = {
  api: recordsApi,
  prompt: (message, initial) => window.prompt(message, initial),
};
const message = (error: unknown) => (error instanceof Error ? error.message : '每日记录操作失败');

export function createCheckinController(
  context: DomainContext,
  dependencies: CheckinDependencies = defaults,
): DomainController {
  const state = createCheckinState();
  const view = createCheckinView(context.document, context.shell);
  const disposers: Array<() => void> = [];
  let started = false;
  async function open(day = '', incoming?: MealEstimate): Promise<void> {
    state.day = day || localDateISO();
    state.current = null;
    state.meals = [];
    state.loadedFields.clear();
    state.status = 'loading';
    state.error = undefined;
    context.shell.openModal('checkin');
    view.render(state, true);
    const operation = context.operations.begin('checkin-load');
    try {
      const response = await dependencies.api.checkin(state.day, operation.signal);
      if (!operation.isCurrent()) return;
      const current = isRecord(response.checkin) ? response.checkin : null;
      state.current = current;
      state.loadedFields = new Set(Object.keys(current ?? {}));
      state.meals = Array.isArray(current?.meal_estimates)
        ? current.meal_estimates
            .map((meal) => normalizeMealEstimate(meal, true))
            .filter((meal): meal is MealEstimate => Boolean(meal))
        : [];
      if (incoming) state.meals.push(incoming);
      state.status = 'editing';
      view.render(state, true);
      if (incoming) view.addMealNutrition(incoming);
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.status = 'error';
        state.error = `读取 ${state.day} 的每日记录失败：${message(error)}`;
        if (incoming) state.meals.push(incoming);
        view.render(state, true);
        if (incoming) view.addMealNutrition(incoming);
      }
    }
  }
  function close(): void {
    context.operations.cancel('checkin-load');
    context.operations.cancel('checkin-save');
    context.shell.closeModal('checkin');
    state.meals = [];
    state.status = 'idle';
    view.render(state);
  }
  function addManualMeal(): void {
    const slot = dependencies.prompt('餐次（早餐/午餐/晚餐/加餐）', '晚餐');
    if (slot === null) return;
    const meal = normalizeMealEstimate(
      {
        source: 'manual_meal',
        meal_slot: slot.trim(),
        confidence: 'high',
        user_confirmed: true,
        items: [
          {
            name: '手动记录',
            portion: '未记录',
            calories_kcal: 0,
            protein_g: 0,
            carbs_g: 0,
            fat_g: 0,
          },
        ],
      },
      true,
    );
    if (meal) {
      state.meals.push(meal);
      view.render(state);
    }
  }
  async function save(payload: Record<string, unknown>): Promise<void> {
    if (state.status === 'saving') return;
    const actual = Object.entries(payload).filter(
      ([key, value]) =>
        key !== 'date' &&
        value !== null &&
        value !== '' &&
        !(key === 'cheat_meal' && value === false) &&
        !(key === 'meal_estimates' && Array.isArray(value) && !value.length),
    );
    if (!actual.length) {
      view.showError('请至少填写一项每日记录。');
      return;
    }
    const validation = validateMealTotals(payload, state.meals);
    if (validation) {
      view.showError(validation);
      return;
    }
    state.status = 'saving';
    view.setBusy(true);
    const operation = context.operations.begin('checkin-save');
    try {
      const result = await dependencies.api.saveCheckin(payload, operation.signal);
      if (!operation.isCurrent()) return;
      close();
      context.shell.toast.success(
        `${result.created ? '已保存' : '已更新'} ${String(result.date ?? state.day)} 的每日记录。${String(result.notice ?? '')}`,
      );
      context.events.emit('data:refresh');
      context.events.emit('health:refresh');
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent()) {
        state.status = 'editing';
        view.showError(message(error), checkinFieldErrors(error));
      }
    } finally {
      view.setBusy(false);
    }
  }
  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onOpen: (day) => void open(day),
          onClose: close,
          onDay: (day) => void open(day),
          onSubmit: (payload) => void save(payload),
          onAddMeal: addManualMeal,
        }),
        context.events.on('checkin:open', ({ day }) => void open(day)),
        context.events.on('checkin:food', (raw) => {
          const meal = normalizeMealEstimate({ ...raw, source: 'food_photo_estimate' });
          if (meal) void open('', meal);
        }),
      );
      view.render(state);
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      context.operations.cancel('checkin-load');
      context.operations.cancel('checkin-save');
    },
  };
}
