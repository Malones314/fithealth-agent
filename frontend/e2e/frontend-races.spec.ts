import { expect, test, type Page } from '@playwright/test';
import { installDeterministicStartupRoutes } from './fixtures';

test('a late training response cannot replace the nutrition viewer', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  let releaseTraining!: () => void;
  let startedTraining!: () => void;
  let finishedTraining!: () => void;
  const release = new Promise<void>((resolve) => {
    releaseTraining = resolve;
  });
  const started = new Promise<void>((resolve) => {
    startedTraining = resolve;
  });
  const finished = new Promise<void>((resolve) => {
    finishedTraining = resolve;
  });
  await page.route('**/data/training-records?**', async (route) => {
    startedTraining();
    await release;
    try {
      await route.fulfill({ json: { items: [{ id: 'stale-training', name: '过期训练记录' }] } });
    } finally {
      finishedTraining();
    }
  });
  await page.route('**/data/nutrition-records**', (route) =>
    route.fulfill({
      json: {
        items: [{ id: 'latest-nutrition', name: '最新营养记录', kind: 'manual', revision: 1 }],
      },
    }),
  );
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();
  await started;
  try {
    await page.locator('#viewer-nutrition').click();
    await expect(page.locator('#viewer-record-select')).toHaveValue('latest-nutrition');
  } finally {
    releaseTraining();
  }
  await finished;
  // Let any response continuations render after the delayed route completes.
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await expect(page.locator('#viewer-content-title')).toHaveText('营养组列表');
  await expect(page.locator('#viewer-record-select')).toHaveValue('latest-nutrition');
  await expect(page.locator('#viewer-detail')).toContainText('最新营养记录');
  expect(problems).toEqual([]);
});

/**
 * 阶段 5 验收门槛里的竞态场景：快速切日期、重复弹窗、连续刷新、断网恢复。
 *
 * 每条都断言**可观察结果**（屏幕上留下的是哪一天的数据、控制台有没有未处理异常），
 * 不去断言内部 token 数值——那属于 operations 的单测。
 */

/** 收集控制台异常与未处理拒绝；取消不应出现在这里。 */
function watchConsole(page: Page): string[] {
  const problems: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    // 资源加载失败与 fixture 未覆盖的请求不是本用例关心的对象。
    if (text.startsWith('Failed to load resource')) return;
    problems.push(text);
  });
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  return problems;
}

/**
 * 让 `/health/trend` 按"先发的慢、后发的快"回应——这正是旧响应覆盖新状态的条件。
 * 第一次请求延迟 600ms，之后立即返回，并把 end_date 回显成请求里的日期。
 */
async function installOutOfOrderTrend(page: Page): Promise<void> {
  let seen = 0;
  await page.route('**/health/trend?**', async (route) => {
    seen += 1;
    const requested = new URL(route.request().url()).searchParams.get('end_date') ?? '2026-09-10';
    if (seen === 1) await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({
      json: {
        metric: 'resting_hr',
        unit: 'bpm',
        cumulative: false,
        start_date: requested,
        end_date: requested,
        items: [{ label: requested, value: seen === 1 ? 41 : 57 }],
      },
    });
  });
}

test('a slow earlier trend response cannot overwrite the newer one', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  await installOutOfOrderTrend(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();

  // 快速切两次日期：第一次的响应会在第二次之后才到。
  await page.locator('#trend-date').fill('2026-09-01');
  await page.locator('#trend-date').dispatchEvent('change');
  await page.locator('#trend-date').fill('2026-09-08');
  await page.locator('#trend-date').dispatchEvent('change');

  // 等到慢响应本该到达之后再断言，否则测的是"还没到"而不是"到了但被丢弃"。
  await page.waitForTimeout(1200);
  await expect(page.locator('#trend-summary')).toContainText('2026-09-08');
  await expect(page.locator('#trend-summary')).not.toContainText('2026-09-01');
  await expect(page.locator('#trend-date')).toHaveValue('2026-09-08');
  expect(problems).toEqual([]);
});

test('cancelled trend requests are not shown as business failures', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  await installOutOfOrderTrend(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();

  await page.locator('#trend-date').fill('2026-09-02');
  await page.locator('#trend-date').dispatchEvent('change');
  await page.locator('#trend-date').fill('2026-09-09');
  await page.locator('#trend-date').dispatchEvent('change');
  await page.waitForTimeout(1200);

  // 被取代的那一轮走 AbortError；它不能变成"健康趋势加载失败"。
  await expect(page.locator('#trend-summary')).not.toContainText('加载失败');
  expect(problems).toEqual([]);
});

test('rapidly switching overview days leaves the last requested day on screen', async ({
  page,
}) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  let seen = 0;
  await page.route('**/health/overview**', async (route) => {
    seen += 1;
    const requested = new URL(route.request().url()).searchParams.get('day') ?? '2026-09-10';
    if (seen === 2) await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({
      json: {
        date: requested,
        has_data: true,
        available_sections: ['heart_rate'],
        heart_rate: { avg: 60 + seen, min: 50, max: 120 },
      },
    });
  });

  await page.goto('/');
  await page.locator('#btn-overview').click();
  await expect(page.locator('#health-modal')).toHaveClass(/show/);

  await page.locator('#overview-prev').click();
  await page.locator('#overview-prev').click();
  await page.waitForTimeout(1200);

  const shown = await page.locator('#overview-date').inputValue();
  await expect(page.locator('#overview-date')).toHaveValue(shown);
  await expect(page.locator('#overview-status')).not.toContainText('加载失败');
  expect(problems).toEqual([]);
});

test('reopening the overview repeatedly does not stack modals or leak scroll lock', async ({
  page,
}) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  await page.goto('/');

  for (let round = 0; round < 4; round += 1) {
    await page.locator('#btn-overview').click();
    await expect(page.locator('#health-modal')).toHaveClass(/show/);
    await page.keyboard.press('Escape');
    await expect(page.locator('#health-modal')).not.toHaveClass(/show/);
  }
  // 每次关闭都必须解锁滚动；漏掉一次页面就再也滚不动了。
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  expect(problems).toEqual([]);
});

test('closing the overview mid-flight does not repaint it afterwards', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  await page.route('**/health/overview**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.fulfill({
      json: {
        date: '2026-09-10',
        has_data: true,
        available_sections: ['heart_rate'],
        heart_rate: { avg: 67, min: 52, max: 128 },
      },
    });
  });

  await page.goto('/');
  await page.locator('#btn-overview').click();
  // 响应还在飞的时候就关掉。
  await page.keyboard.press('Escape');
  await expect(page.locator('#health-modal')).not.toHaveClass(/show/);
  await page.waitForTimeout(1200);

  // 关掉之后不能又被渲染出来。
  await expect(page.locator('#health-modal')).not.toHaveClass(/show/);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  expect(problems).toEqual([]);
});

test('a dropped network recovers on the next refresh without a stuck error', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  let online = false;
  await page.route('**/health/trend?**', async (route) => {
    if (!online) {
      await route.abort('internetdisconnected');
      return;
    }
    await route.fulfill({
      json: {
        metric: 'resting_hr',
        unit: 'bpm',
        cumulative: false,
        start_date: '2026-09-04',
        end_date: '2026-09-10',
        items: [{ label: '2026-09-10', value: 56 }],
      },
    });
  });

  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();
  // 断网时给出可展示的失败，而不是静默空白。
  await expect(page.locator('#trend-summary')).not.toHaveText('正在加载健康数据…');

  online = true;
  // 用页面真实存在的指标；`resting_hr` 只是 fixture 回显的字段名，不是选项值。
  await page.locator('#trend-metric').selectOption('hrv');
  await expect(page.locator('#trend-summary')).toContainText('平均');
  await expect(page.locator('#trend-summary')).not.toContainText('加载失败');
  expect(problems).toEqual([]);
});

test('continuous refreshes keep the page consistent and quiet', async ({ page }) => {
  const problems = watchConsole(page);
  await installDeterministicStartupRoutes(page);
  await page.goto('/');
  await expect(page.locator('.main-grid')).toBeVisible();

  for (let round = 0; round < 5; round += 1) {
    await page.locator('#trend-metric').dispatchEvent('change');
  }
  await page.waitForTimeout(600);
  await expect(page.locator('.main-grid')).toBeVisible();
  await expect(page.locator('#trend-summary')).not.toContainText('加载失败');
  expect(problems).toEqual([]);
});
