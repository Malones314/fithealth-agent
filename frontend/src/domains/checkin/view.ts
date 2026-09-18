import type { AppShell } from '../../app/shell';
import {
  mealSummary,
  mealTotals,
  nutritionNumber,
  updateMealTotals,
  type MealEstimate,
  type NutrientField,
} from '../../shared/nutrition';
import type { JsonObject } from '../../shared/types';
import type { CheckinDomainState } from './types';

export interface CheckinViewHandlers {
  onOpen(day?: string): void;
  onClose(): void;
  onDay(day: string): void;
  onSubmit(payload: JsonObject): void;
  onAddMeal(): void;
}
export interface CheckinView {
  bind(handlers: CheckinViewHandlers): () => void;
  render(state: CheckinDomainState, reset?: boolean): void;
  readPayload(state: CheckinDomainState): JsonObject;
  addMealNutrition(meal: MealEstimate): void;
  setBusy(busy: boolean): void;
  showError(message: string, fields?: Record<string, string>): void;
}
function required<T extends Element>(document: Document, selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Checkin element not found: ${selector}`);
  return element;
}
const nutrientFields: Array<[NutrientField, number, boolean]> = [
  ['calories_kcal', 20000, true],
  ['protein_g', 1000, false],
  ['carbs_g', 2000, false],
  ['fat_g', 1000, false],
];

export function createCheckinView(document: Document, shell: AppShell): CheckinView {
  const form = required<HTMLFormElement>(document, '#checkin-form');
  const date = required<HTMLInputElement>(document, '#checkin-date');
  const hint = required<HTMLElement>(document, '#checkin-hint');
  const section = required<HTMLElement>(document, '#meal-estimate-section');
  const mealsRoot = required<HTMLElement>(document, '#meal-estimates');
  const submit = form.querySelector<HTMLButtonElement>('[type="submit"]');
  if (!submit) throw new Error('Checkin submit button not found');
  const listeners: Array<[EventTarget, string, EventListener]> = [];
  let current: CheckinDomainState | null = null;
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };
  const input = (name: string) =>
    form.elements.namedItem(name) as HTMLInputElement | HTMLTextAreaElement | null;
  function adjustNutrition(
    before: Record<NutrientField, number>,
    after: Record<NutrientField, number>,
  ): void {
    nutrientFields.forEach(([field, maximum, integer]) => {
      const node = input(field);
      if (!node) return;
      const base = node.value === '' ? 0 : nutritionNumber(node.value, maximum, integer);
      node.value = String(nutritionNumber(base + after[field] - before[field], maximum, integer));
    });
  }
  function renderMeals(state: CheckinDomainState): void {
    section.hidden = state.meals.length === 0;
    mealsRoot.hidden = state.meals.length === 0;
    mealsRoot.replaceChildren();
    state.meals.forEach((meal, mealIndex) => {
      const panel = document.createElement('section');
      panel.className = 'meal-estimate';
      const head = document.createElement('div');
      head.className = 'meal-estimate-head';
      const title = document.createElement('strong');
      const labels: Record<string, string> = {
        food_photo_estimate: '照片估算',
        manual_nutrition: '手动营养',
        text_nutrition: '文字营养',
        manual_meal: '手动餐次',
      };
      title.textContent = `${labels[meal.source] ?? '营养记录'} ${mealIndex + 1}`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'small-delete';
      remove.textContent = '移除';
      remove.addEventListener('click', () => {
        if (meal.applied_to_form)
          adjustNutrition(mealTotals(meal), {
            calories_kcal: 0,
            protein_g: 0,
            carbs_g: 0,
            fat_g: 0,
          });
        state.meals.splice(mealIndex, 1);
        renderMeals(state);
      });
      head.append(title, remove);
      panel.append(head);
      const meta = document.createElement('div');
      meta.className = 'meal-estimate-meta';
      meta.textContent =
        meal.source === 'food_photo_estimate'
          ? `可信度：${{ low: '低', medium: '中', high: '高' }[meal.confidence]}；热量范围：${meal.range_low_kcal}-${meal.range_high_kcal} kcal${meal.assumptions.length ? `；假设：${meal.assumptions.join('；')}` : ''}`
          : '可直接编辑本组的食物、份量和营养数据';
      panel.append(meta);
      const columns: Array<[keyof MealEstimate['items'][number], string, string, number]> = [
        ['name', '食物', 'text', 0],
        ['portion', '份量', 'text', 0],
        ['calories_kcal', '热量 (kcal)', 'number', 1],
        ['protein_g', '蛋白质 (g)', 'number', 0.1],
        ['carbs_g', '碳水 (g)', 'number', 0.1],
        ['fat_g', '脂肪 (g)', 'number', 0.1],
      ];
      const header = document.createElement('div');
      header.className = 'meal-item-grid meal-item-header';
      columns.forEach(([, label]) => {
        const node = document.createElement('span');
        node.textContent = label;
        header.append(node);
      });
      panel.append(header);
      meal.items.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'meal-item-grid';
        columns.forEach(([field, label, type, step]) => {
          const node = document.createElement('input');
          node.type = type;
          node.value = String(item[field]);
          node.placeholder = label;
          node.setAttribute('aria-label', label);
          if (type === 'number') {
            node.min = '0';
            node.step = String(step);
          }
          node.addEventListener('input', () => {
            const before = mealTotals(meal);
            if (type === 'number')
              item[field] = nutritionNumber(
                node.value,
                field === 'calories_kcal' ? 20000 : field === 'carbs_g' ? 2000 : 1000,
                field === 'calories_kcal',
              ) as never;
            else item[field] = node.value.slice(0, 80) as never;
            updateMealTotals(meal);
            if (meal.applied_to_form) adjustNutrition(before, mealTotals(meal));
            summary.textContent = mealSummary(meal);
          });
          row.append(node);
        });
        panel.append(row);
      });
      const summary = document.createElement('div');
      summary.className = 'meal-estimate-summary';
      summary.textContent = mealSummary(meal);
      panel.append(summary);
      if (meal.confidence === 'low') {
        const confirmation = document.createElement('label');
        confirmation.className = 'meal-confirm';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = meal.user_confirmed;
        checkbox.addEventListener('change', () => {
          meal.user_confirmed = checkbox.checked;
        });
        confirmation.append(
          checkbox,
          document.createTextNode('我已查看不确定因素，仍要保存这条低可信度估算'),
        );
        panel.append(confirmation);
      }
      mealsRoot.append(panel);
    });
  }
  return {
    bind(handlers) {
      listen(required(document, '#btn-checkin'), 'click', () => handlers.onOpen());
      listen(required(document, '#open-checkin'), 'click', () => handlers.onOpen());
      listen(required(document, '#checkin-modal-close'), 'click', handlers.onClose);
      listen(required(document, '#checkin-cancel'), 'click', handlers.onClose);
      shell.modal('checkin').onRequestClose = handlers.onClose;
      listen(date, 'change', () => {
        if (date.value) handlers.onDay(date.value);
      });
      listen(required(document, '#add-nutrition-record'), 'click', handlers.onAddMeal);
      listen(form, 'input', (event) => {
        const target = event.target;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)
          target.setCustomValidity('');
      });
      listen(form, 'submit', (event) => {
        event.preventDefault();
        if (current) handlers.onSubmit(this.readPayload(current));
      });
      return () =>
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
    },
    render(state, reset = false) {
      current = state;
      document.documentElement.dataset.checkinEditing = String(state.status === 'editing');
      if (reset) {
        form.reset();
        date.value = state.day;
        Object.entries(state.current ?? {}).forEach(([key, value]) => {
          if (key === 'meal_estimates') return;
          const node = input(key);
          if (!node) return;
          if (node instanceof HTMLInputElement && node.type === 'checkbox')
            node.checked = Boolean(value);
          else node.value = value == null ? '' : String(value);
        });
      }
      renderMeals(state);
      if (state.status === 'loading') {
        hint.textContent = '正在读取每日记录…';
        hint.classList.add('show');
      } else if (state.status === 'error') this.showError(state.error ?? '每日记录加载失败');
      else if (state.current) {
        hint.textContent = `已载入 ${state.day} 的记录，保存将更新这一条（留空的字段保持不变）。`;
        hint.classList.add('show');
      } else {
        hint.textContent = '';
        hint.classList.remove('show');
      }
    },
    readPayload(state) {
      const payload: JsonObject = {};
      new FormData(form).forEach((value, key) => {
        if (value !== '') payload[key] = String(value);
      });
      const checkbox = input('cheat_meal') as HTMLInputElement | null;
      payload.cheat_meal = Boolean(checkbox?.checked);
      payload.meal_estimates = state.meals;
      for (const key of [
        'weight_kg',
        'energy_level',
        'fatigue_level',
        'pain_level',
        'sleep_quality',
        'calories_kcal',
        'protein_g',
        'carbs_g',
        'fat_g',
        'training_rpe',
        'training_completion_pct',
        'note',
      ])
        if (state.loadedFields.has(key) && !String(input(key)?.value ?? '').trim())
          payload[key] = null;
      return payload;
    },
    addMealNutrition(meal) {
      adjustNutrition({ calories_kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0 }, mealTotals(meal));
      meal.applied_to_form = true;
    },
    setBusy(busy) {
      submit.disabled = busy;
    },
    showError(message, fields = {}) {
      hint.textContent = message;
      hint.classList.add('show');
      Object.entries(fields).forEach(([name, error]) => {
        const node = input(name);
        node?.setCustomValidity(error);
      });
      const first = Object.keys(fields)[0];
      input(first)?.focus();
    },
  };
}
