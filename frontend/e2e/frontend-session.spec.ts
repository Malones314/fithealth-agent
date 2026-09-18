import { expect, test } from '@playwright/test';
import { installDeterministicStartupRoutes } from './fixtures';

test('session owns settings, connectivity and logout lifecycle', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  let settingsUpdates = 0;
  let logoutCalls = 0;
  let logoutBody: unknown;

  await page.route('**/settings/external-models', async (route) => {
    if (route.request().method() === 'PUT') {
      settingsUpdates += 1;
      await route.fulfill({
        json: {
          external_models_enabled: true,
          disclosure: [{ name: '测试模型', data: '对话' }],
          local_features: ['训练编辑'],
        },
      });
      return;
    }
    await route.fulfill({ json: { external_models_enabled: false } });
  });
  await page.route('**/settings/llm-connectivity', (route) =>
    route.fulfill({ json: { ok: true, model: 'fixture-model', latency_ms: 8 } }),
  );
  await page.route('**/logout', async (route) => {
    logoutCalls += 1;
    logoutBody = route.request().postDataJSON();
    await route.fulfill({ json: { status: 'no_messages' } });
  });

  await page.goto('/');
  await expect(page.locator('.session-intro')).toContainText('固定测试会话');
  await expect(page.locator('#message')).toHaveAttribute('placeholder', /联网模型已关闭/);

  await page.locator('#btn-data').click();
  await expect(page.locator('#external-models-toggle')).toBeVisible();
  await page.locator('#external-models-toggle').check();
  await expect.poll(() => settingsUpdates).toBe(1);
  await expect(page.locator('#message')).toHaveAttribute('placeholder', /把第2和第3组合并/);
  await page.locator('#data-modal-close').click();

  await page.locator('#btn-llm-test').click();
  await expect(page.locator('#chat')).toContainText('fixture-model');

  await page.locator('#btn-logout').click();
  await page.locator('#btn-logout').click({ force: true });
  await expect(page.locator('#ended-overlay')).toHaveClass(/show/);
  expect(logoutCalls).toBe(1);
  expect(logoutBody).toEqual({ messages: [] });
});
