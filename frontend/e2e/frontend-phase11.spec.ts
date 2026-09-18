import { expect, test } from '@playwright/test';

test('data management sections retain their collapsible order', async ({ page }) => {
  await page.goto('/');

  const collapsibleTitles = await page.locator('details.data-collapsible h3').allTextContents();
  expect(collapsibleTitles.map((title) => title.trim())).toEqual([
    '训练计划',
    '训练记录',
    '全天健康与睡眠',
    '数据文件对账',
    '临时记忆',
    '酸痛与伤病记录',
  ]);
  await expect(page.locator('.data-section h3').last()).toHaveText('外部模型与数据外发');
});
