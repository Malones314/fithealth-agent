import { expect, test } from '@playwright/test';
import { installDeterministicStartupRoutes } from './fixtures';

test('main page visual baseline', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      consoleErrors.push(message.text());
    }
  });
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();
  await expect(page.locator('.session-intro')).toBeVisible();
  await expect(page).toHaveScreenshot('main-page.png', { fullPage: true });
  expect(consoleErrors).toEqual([]);
});

test('daily health overview visual baseline', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await page.locator('#btn-overview').click();
  await expect(page.locator('#health-modal')).toHaveClass(/show/);
  await expect(page.locator('#overview-grid')).not.toBeEmpty();
  await expect(page).toHaveScreenshot('health-overview.png', { fullPage: true });
});

test('data management visual baseline', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await page.locator('#btn-data').click();
  await expect(page.locator('#data-modal')).toHaveClass(/show/);
  await expect(page.locator('#data-audit-list')).toContainText('文件与数据库引用一致');
  await expect(page).toHaveScreenshot('data-management.png', { fullPage: true });
});

test('multi-zip activity picker visual baseline', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.route('**/upload_health', (route) =>
    route.fulfill({
      json: {
        status: 'ok',
        activities: [
          { zip: 'Health-2026-09-09.zip', name: 'morning-run.fit' },
          { zip: 'Health-2026-09-10.zip', name: 'strength.fit' },
        ],
      },
    }),
  );
  await page.goto('/');
  await page.locator('#file-input').setInputFiles([
    { name: 'Health-2026-09-09.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture') },
    { name: 'Health-2026-09-10.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture') },
  ]);
  await expect(page.locator('#activity-picker-modal')).toHaveClass(/show/);
  await expect(page.locator('.activity-picker-item')).toHaveCount(2);
  await expect(page).toHaveScreenshot('activity-picker.png', { fullPage: true });
});
