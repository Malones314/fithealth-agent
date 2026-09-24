import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const e2ePort = process.env.FITHEALTH_E2E_PORT ?? '10019';
const baseURL = process.env.FITHEALTH_E2E_BASE_URL ?? `http://127.0.0.1:${e2ePort}`;
const reuseExistingServer = process.env.FITHEALTH_E2E_REUSE_SERVER === '1';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    baseURL,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    colorScheme: 'dark',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}-{projectName}{ext}',
  projects: [
    { name: 'desktop-1440', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'tablet-768', use: { viewport: { width: 768, height: 1024 } } },
    { name: 'mobile-390', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `python -m uvicorn main:app --host 127.0.0.1 --port ${e2ePort}`,
    cwd: repositoryRoot,
    env: {
      FITHEALTH_DATA_DIR: path.join(repositoryRoot, '.test-tmp', 'frontend-baseline'),
    },
    url: `${baseURL}/health/storage-status`,
    reuseExistingServer,
    timeout: 30_000,
  },
});
