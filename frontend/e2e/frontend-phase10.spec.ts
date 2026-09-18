import { expect, test } from '@playwright/test';
import { installDeterministicStartupRoutes } from './fixtures';

test('health and checkin domains own the user workflow', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  let saved: Record<string, unknown> | undefined;
  await page.route('**/data/checkins/*', (route) =>
    route.fulfill({
      json: {
        checkin: {
          date: '2026-09-14',
          weight_kg: 70,
          energy_level: 7,
          note: '已有记录',
          meal_estimates: [],
        },
      },
    }),
  );
  await page.route('**/data/checkins', async (route) => {
    saved = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { created: false, date: '2026-09-14' } });
  });

  await page.goto('/');
  await expect(page.locator('#trend-metric option[value="total_calories"]')).toHaveCount(1);
  await page.locator('#btn-checkin').click();
  await expect(page.locator('#checkin-modal')).toHaveClass(/show/);
  await expect(page.locator('#checkin-hint')).toContainText('已载入');
  await expect(page.locator('[name="weight_kg"]')).toHaveValue('70');

  await page.locator('[name="weight_kg"]').fill('71.2');
  await page.locator('#checkin-form [type="submit"]').click();
  await expect(page.locator('#checkin-modal')).not.toHaveClass(/show/);
  await expect(page.locator('#chat')).toContainText('已更新 2026-09-14 的每日记录');
  expect(saved).toMatchObject({ date: '2026-09-14', weight_kg: '71.2', note: '已有记录' });
});
