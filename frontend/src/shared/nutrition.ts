import type { JsonObject } from './types';

export type NutrientField = 'calories_kcal' | 'protein_g' | 'carbs_g' | 'fat_g';
export interface NutritionItem extends JsonObject {
  name: string;
  portion: string;
  calories_kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
}
export interface MealEstimate extends JsonObject {
  source: string;
  items: NutritionItem[];
  meal_slot: string;
  photographed_at: string;
  total_kcal: number;
  protein_g: number;
  carbs_g: number;
  fat_g: number;
  range_low_kcal: number;
  range_high_kcal: number;
  confidence: 'low' | 'medium' | 'high';
  assumptions: string[];
  user_confirmed: boolean;
  analysis_token: string;
  applied_to_form: boolean;
}

export function nutritionNumber(value: unknown, maximum: number, integer = false): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  const safe = Math.max(0, Math.min(maximum, numeric));
  return integer ? Math.round(safe) : Math.round(safe * 10) / 10;
}

export function updateMealTotals(meal: MealEstimate): MealEstimate {
  meal.total_kcal = meal.items.reduce(
    (sum, item) => sum + nutritionNumber(item.calories_kcal, 20000, true),
    0,
  );
  for (const field of ['protein_g', 'carbs_g', 'fat_g'] as const) {
    const maximum = field === 'carbs_g' ? 2000 : 1000;
    meal[field] =
      Math.round(
        meal.items.reduce((sum, item) => sum + nutritionNumber(item[field], maximum), 0) * 10,
      ) / 10;
  }
  if (!meal.range_low_kcal || !meal.range_high_kcal) {
    meal.range_low_kcal = Math.round(meal.total_kcal * 0.8);
    meal.range_high_kcal = Math.round(meal.total_kcal * 1.2);
  }
  return meal;
}

export function normalizeMealEstimate(raw: unknown, applied = false): MealEstimate | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as JsonObject;
  if (!Array.isArray(value.items) || !value.items.length) return null;
  const items = value.items.slice(0, 12).map((entry) => {
    const item = entry && typeof entry === 'object' ? (entry as JsonObject) : {};
    return {
      name:
        String(item.name ?? '')
          .trim()
          .slice(0, 80) || '未命名食物',
      portion:
        String(item.portion ?? '')
          .trim()
          .slice(0, 80) || '份量不确定',
      calories_kcal: nutritionNumber(item.calories_kcal, 20000, true),
      protein_g: nutritionNumber(item.protein_g, 1000),
      carbs_g: nutritionNumber(item.carbs_g, 2000),
      fat_g: nutritionNumber(item.fat_g, 1000),
    };
  });
  const allowed = ['food_photo_estimate', 'manual_nutrition', 'text_nutrition', 'manual_meal'];
  return updateMealTotals({
    source: allowed.includes(String(value.source)) ? String(value.source) : 'food_photo_estimate',
    items,
    meal_slot: String(value.meal_slot ?? ''),
    photographed_at: String(value.photographed_at ?? ''),
    total_kcal: nutritionNumber(value.total_kcal, 20000, true),
    protein_g: nutritionNumber(value.protein_g, 1000),
    carbs_g: nutritionNumber(value.carbs_g, 2000),
    fat_g: nutritionNumber(value.fat_g, 1000),
    range_low_kcal: nutritionNumber(value.range_low_kcal, 20000, true),
    range_high_kcal: nutritionNumber(value.range_high_kcal, 20000, true),
    confidence: ['medium', 'high'].includes(String(value.confidence))
      ? (value.confidence as 'medium' | 'high')
      : 'low',
    assumptions: Array.isArray(value.assumptions)
      ? value.assumptions
          .map(String)
          .map((item) => item.slice(0, 160))
          .slice(0, 6)
      : [],
    user_confirmed: Boolean(value.user_confirmed),
    analysis_token: String(value.analysis_token ?? '').slice(0, 200),
    applied_to_form: applied || Boolean(value.applied_to_form),
  });
}

export function mealTotals(meal: MealEstimate): Record<NutrientField, number> {
  return {
    calories_kcal: meal.total_kcal,
    protein_g: meal.protein_g,
    carbs_g: meal.carbs_g,
    fat_g: meal.fat_g,
  };
}

export function mealSummary(meal: MealEstimate): string {
  return `合计：${meal.total_kcal} kcal；蛋白质 ${meal.protein_g} g；碳水 ${meal.carbs_g} g；脂肪 ${meal.fat_g} g`;
}

export function validateMealTotals(payload: JsonObject, meals: MealEstimate[]): string | null {
  if (meals.some((meal) => meal.confidence === 'low' && !meal.user_confirmed))
    return '请确认或移除每一条低可信度餐食估算。';
  if (!meals.length) return null;
  const totals = meals.reduce(
    (sum, meal) => ({
      calories_kcal: sum.calories_kcal + meal.total_kcal,
      protein_g: sum.protein_g + meal.protein_g,
      carbs_g: sum.carbs_g + meal.carbs_g,
      fat_g: sum.fat_g + meal.fat_g,
    }),
    { calories_kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0 },
  );
  const labels: Record<NutrientField, string> = {
    calories_kcal: '热量',
    protein_g: '蛋白质',
    carbs_g: '碳水',
    fat_g: '脂肪',
  };
  for (const field of Object.keys(labels) as NutrientField[]) {
    const tolerance = field === 'calories_kcal' ? 0.5 : 0.05;
    if (
      !Number.isFinite(Number(payload[field])) ||
      Math.abs(Number(payload[field]) - totals[field]) > tolerance
    )
      return `${labels[field]}合计与营养组明细不一致，请修改营养组或合计后再保存。`;
  }
  return null;
}
