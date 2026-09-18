import { expect, test } from '@playwright/test';

test('built homepage and local static assets load without external requests', async ({ page }) => {
  const externalRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
      externalRequests.push(request.url());
    }
  });

  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await expect(page.locator('.main-grid')).toBeVisible();

  const assetUrls = await page
    .locator('script[src], link[rel="stylesheet"][href]')
    .evaluateAll((nodes) =>
      nodes.map((node) => (node as HTMLScriptElement).src || (node as HTMLLinkElement).href),
    );
  const assetResponses = await Promise.all(assetUrls.map((url) => page.request.get(url)));
  expect(assetResponses.every((assetResponse) => assetResponse.ok)).toBe(true);
  expect(externalRequests).toEqual([]);
});
