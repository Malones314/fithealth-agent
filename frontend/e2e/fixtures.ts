import { type Page } from '@playwright/test';

/**
 * 阶段 0 冻结的确定性启动 fixture，被视觉基线与阶段 4 布局检查共用。
 * 动态时间、健康数值和 request token 都固定，截图与布局断言才可重复。
 */
export async function installDeterministicStartupRoutes(page: Page): Promise<void> {
  await page.route('**/settings/external-models', (route) =>
    route.fulfill({ json: { external_models_enabled: false } }),
  );
  await page.route('**/session/intro?**', (route) =>
    route.fulfill({
      json: {
        message: '固定测试会话：今天以恢复和动作质量为优先。',
        garmin_recovery_hours: 0,
        soreness_prompt_regions: [],
      },
    }),
  );
  await page.route('**/profile/status', (route) =>
    route.fulfill({ json: { complete: true, missing_fields: [], prompt: '' } }),
  );
  await page.route('**/health/storage-status', (route) =>
    route.fulfill({ json: { available: true, message: '' } }),
  );
  await page.route('**/health/trend?**', (route) =>
    route.fulfill({
      json: {
        metric: 'resting_hr',
        unit: 'bpm',
        cumulative: false,
        start_date: '2026-09-04',
        end_date: '2026-09-10',
        items: [
          { label: '2026-09-04', value: 58 },
          { label: '2026-09-07', value: 57 },
          { label: '2026-09-10', value: 56 },
        ],
      },
    }),
  );
  await page.route('**/workout_state', (route) => route.fulfill({ json: { has_workout: false } }));
  await page.route('**/workout_state/quarantined', (route) =>
    route.fulfill({ json: { files: [] } }),
  );
  await page.route('**/data/overview', (route) =>
    route.fulfill({
      json: {
        records: [],
        daily_records: [],
        plans: [],
        memories: [],
        soreness_reports: [],
        health_imports: [],
        profile: {},
        profile_complete: false,
        has_pending_workout: false,
      },
    }),
  );
  await page.route('**/health/imports/raw-audit', (route) =>
    route.fulfill({ json: { missing: [], orphans: [] } }),
  );
  await page.route('**/data/hr-streams/audit', (route) =>
    route.fulfill({ json: { missing: [], orphans: [] } }),
  );
  await page.route('**/workout_state/quarantined?**', (route) =>
    route.fulfill({ json: { files: [] } }),
  );
  await page.route('**/data/recovery-points', (route) => route.fulfill({ json: { points: [] } }));
  await page.route('**/health/overview', (route) =>
    route.fulfill({
      json: {
        date: '2026-09-10',
        has_data: true,
        available_sections: ['sleep', 'heart_rate', 'activity'],
        sleep: { duration_min: 438, score: 82, awake_min: 22 },
        heart_rate: { avg: 67, min: 52, max: 128 },
        activity: { steps: 8642, distance_m: 6310, active_calories: 410, total_calories: 1960 },
      },
    }),
  );
}
