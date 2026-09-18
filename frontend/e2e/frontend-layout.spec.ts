import { expect, test, type Page } from '@playwright/test';
import { installDeterministicStartupRoutes } from './fixtures';

/**
 * 阶段 4 的可测量门槛：
 *   - 三种 viewport 下无横向页面溢出；
 *   - 固定格式控件（发送、上传、日期、周期切换）尺寸不因动态文本改变；
 *   - 模态框统一走共享 Modal：Escape 关闭、焦点归还触发按钮、焦点圈定在框内。
 *
 * viewport 由 playwright.config.ts 的三个 project 提供，所以这里不写死尺寸。
 */

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const documentWidth = document.documentElement.scrollWidth;
    const viewportWidth = document.documentElement.clientWidth;
    return documentWidth - viewportWidth;
  });
}

/**
 * 超出视口右边缘、且**不在**任何横向滚动容器里的元素——用于定位真正的页面溢出源。
 *
 * 窄屏下 `.header-actions`、`.data-viewer-switch` 等是刻意的横向滚动区（`overflow-x:auto`），
 * 里面的元素本来就会伸到视口之外。把它们算成溢出会让这条门槛变成噪音，所以先剔除。
 */
async function overflowingElements(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const insideScroller = (element: HTMLElement): boolean => {
      for (let node = element.parentElement; node; node = node.parentElement) {
        const overflowX = getComputedStyle(node).overflowX;
        if (overflowX === 'auto' || overflowX === 'scroll') return true;
      }
      return false;
    };
    return Array.from(document.body.querySelectorAll<HTMLElement>('*'))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        if (rect.right <= limit + 1) return false;
        return !insideScroller(element);
      })
      .slice(0, 8)
      .map((element) => `${element.tagName.toLowerCase()}#${element.id}.${element.className}`);
  });
}

async function boxOf(page: Page, selector: string): Promise<{ width: number; height: number }> {
  const box = await page.locator(selector).boundingBox();
  if (!box) throw new Error(`no box for ${selector}`);
  return { width: Math.round(box.width), height: Math.round(box.height) };
}

test('the main page does not overflow horizontally', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();
  expect(await overflowingElements(page)).toEqual([]);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

for (const [name, trigger, root] of [
  ['daily health overview', '#btn-overview', '#health-modal'],
  ['data management', '#btn-data', '#data-modal'],
] as const) {
  test(`${name} modal does not overflow horizontally`, async ({ page }) => {
    await installDeterministicStartupRoutes(page);
    await page.goto('/');
    await page.locator(trigger).click();
    await expect(page.locator(root)).toHaveClass(/show/);
    expect(await overflowingElements(page)).toEqual([]);
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  });

  test(`${name} modal closes on Escape and returns focus to its trigger`, async ({ page }) => {
    await installDeterministicStartupRoutes(page);
    await page.goto('/');
    await page.locator(trigger).click();
    await expect(page.locator(root)).toHaveClass(/show/);
    // 打开时页面滚动被锁定，关闭后必须解锁。
    await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');

    await page.keyboard.press('Escape');
    await expect(page.locator(root)).not.toHaveClass(/show/);
    await expect(page.locator(root)).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
    expect(await page.evaluate(() => document.activeElement?.id)).toBe(trigger.slice(1));
  });

  test(`${name} modal traps Tab inside the dialog`, async ({ page }) => {
    await installDeterministicStartupRoutes(page);
    await page.goto('/');
    await page.locator(trigger).click();
    await expect(page.locator(root)).toHaveClass(/show/);
    for (let index = 0; index < 25; index += 1) {
      await page.keyboard.press('Tab');
      const inside = await page.evaluate(
        (selector) => document.querySelector(selector)?.contains(document.activeElement) ?? false,
        root,
      );
      expect(inside, `focus escaped the ${name} dialog after ${index + 1} tabs`).toBe(true);
    }
  });
}

test('fixed-format controls keep their size when dynamic text changes', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();

  const selectors = ['#send', '#file-upload-button', '#food-image-button', '#trend-date'];
  const before = await Promise.all(selectors.map((selector) => boxOf(page, selector)));

  // 灌入长文案：会话状态、趋势摘要和一条很长的通知都变了，控件尺寸不能跟着变。
  await page.evaluate(() => {
    const long = '很长的动态文案'.repeat(20);
    const summary = document.querySelector('#trend-summary');
    if (summary) summary.textContent = long;
    const status = document.querySelector('#session-recovery-status');
    if (status) status.textContent = long;
    const chat = document.querySelector('#chat');
    if (chat) {
      const notice = document.createElement('div');
      notice.className = 'msg system notify-error';
      notice.textContent = long;
      chat.append(notice);
    }
  });

  const after = await Promise.all(selectors.map((selector) => boxOf(page, selector)));
  expect(after).toEqual(before);
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
});

test('the trend period group is a keyboard-navigable tablist', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  const group = page.locator('.trend-periods');
  await expect(group).toHaveAttribute('role', 'tablist');
  await expect(group.locator('.trend-period').first()).toHaveAttribute('aria-selected', 'true');

  await group.locator('.trend-period').first().focus();
  await page.keyboard.press('ArrowRight');
  await expect(group.locator('.trend-period').nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(group.locator('.trend-period').nth(1)).toHaveClass(/active/);
  await expect(group.locator('.trend-period').first()).toHaveAttribute('aria-selected', 'false');
});

test('the data viewer switch is a keyboard-navigable tablist', async ({ page }) => {
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  const group = page.locator('.data-viewer-switch');
  await expect(group).toHaveAttribute('role', 'tablist');
  await expect(page.locator('#viewer-training')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#viewer-nutrition')).toHaveAttribute('aria-selected', 'false');
});

test('no external static requests and no unhandled console errors', async ({ page }) => {
  const external: string[] = [];
  const consoleErrors: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost'].includes(url.hostname)) external.push(request.url());
  });
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      consoleErrors.push(message.text());
    }
  });
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();
  await page.locator('#btn-data').click();
  await page.keyboard.press('Escape');
  expect(external).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
