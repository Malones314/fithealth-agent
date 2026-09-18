import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createHealthController, type HealthDependencies } from './controller';

function mount(): void {
  document.body.innerHTML = `<div id="chat"></div><button id="btn-overview"></button><div id="health-modal" aria-hidden="true"><div role="dialog"><button id="health-modal-close"></button><button id="overview-prev"></button><input id="overview-date"><button id="overview-next"></button><div id="overview-status"></div><div id="overview-grid"></div></div></div><select id="trend-metric"><option value="heart_rate">心率</option><option value="total_calories">热量</option></select><div class="trend-periods"><button class="trend-period" data-period="day"></button><button class="trend-period" data-period="week"></button><button class="trend-period" data-period="month"></button></div><input id="trend-date"><div id="trend-summary"></div><svg id="trend-chart"></svg>`;
}
function context(): DomainContext {
  return {
    state: createAppState(),
    events: createEventBus(),
    shell: createShell(),
    operations: createOperationRegistry(),
    document,
  };
}
function dependencies(): HealthDependencies {
  return {
    api: {
      overview: vi.fn().mockResolvedValue({
        date: '2026-09-14',
        has_data: true,
        available_sections: ['activity'],
        activity: { steps: 1000, total_calories: 1800 },
      }),
      trend: vi.fn().mockResolvedValue({
        metric: 'heart_rate',
        start_date: '2026-09-10',
        end_date: '2026-09-14',
        unit: 'bpm',
        items: [{ label: '2026-09-14', value: 60 }],
      }),
      daily: vi.fn().mockResolvedValue({}),
      range: vi.fn().mockResolvedValue({}),
      sleep: vi.fn().mockResolvedValue({}),
      rawAudit: vi.fn().mockResolvedValue({}),
      importDetails: vi.fn().mockResolvedValue({}),
      deleteImport: vi.fn().mockResolvedValue({ deleted: true }),
      deleteRawOrphan: vi.fn().mockResolvedValue({ deleted: true }),
      storageStatus: vi.fn(),
    },
  } as unknown as HealthDependencies;
}
beforeEach(mount);

describe('health domain', () => {
  it('loads the startup trend and daily overview through typed adapters', async () => {
    const ctx = context();
    const deps = dependencies();
    const controller = createHealthController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() =>
      expect(document.querySelector('#trend-summary')?.textContent).toContain('平均'),
    );
    document.querySelector<HTMLButtonElement>('#btn-overview')!.click();
    await vi.waitFor(() =>
      expect(document.querySelector('#overview-status')?.textContent).toContain('2026-09-14'),
    );
    expect(deps.api.overview).toHaveBeenCalledWith('', expect.any(AbortSignal));
    expect(document.body.textContent).toContain('总消耗 1800 kcal');
    controller.stop?.();
  });

  it('uses the last cumulative value instead of averaging total calories', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.trend = vi.fn().mockResolvedValue({
      metric: 'total_calories',
      cumulative: true,
      start_date: '2026-09-14',
      end_date: '2026-09-14',
      unit: 'kcal',
      items: [
        { label: '12:00', value: 900, resting_calories: 700, active_calories: 200 },
        { label: '23:00', value: 1900, resting_calories: 1500, active_calories: 400 },
      ],
    });
    const controller = createHealthController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() =>
      expect(document.querySelector('#trend-summary')?.textContent).toContain('累计 1900 kcal'),
    );
    expect(document.querySelector('#trend-summary')?.textContent).toContain('静息按日速率均摊');
    controller.stop?.();
  });

  it('prevents an older trend response from replacing the newest selection', async () => {
    const ctx = context();
    const deps = dependencies();
    let first: ((value: unknown) => void) | undefined;
    deps.api.trend = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            first = resolve;
          }),
      )
      .mockResolvedValueOnce({
        metric: 'heart_rate',
        start_date: 'new',
        end_date: '2026-09-14',
        items: [{ label: 'new', value: 70 }],
      });
    const controller = createHealthController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    document.querySelector<HTMLInputElement>('#trend-date')!.value = '2026-09-14';
    document.querySelector<HTMLInputElement>('#trend-date')!.dispatchEvent(new Event('change'));
    await vi.waitFor(() =>
      expect(document.querySelector('#trend-summary')?.textContent).toContain('new'),
    );
    first?.({
      metric: 'heart_rate',
      start_date: 'old',
      end_date: 'old',
      items: [{ label: 'old', value: 30 }],
    });
    await Promise.resolve();
    expect(document.querySelector('#trend-summary')?.textContent).not.toContain('old');
    controller.stop?.();
  });

  it('cancels overview rendering when the modal closes', async () => {
    const ctx = context();
    const deps = dependencies();
    let resolve: ((value: unknown) => void) | undefined;
    deps.api.overview = vi.fn().mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const controller = createHealthController(ctx, deps);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-overview')!.click();
    document.querySelector<HTMLButtonElement>('#health-modal-close')!.click();
    resolve?.({ date: '2026-09-14', has_data: true, available_sections: [] });
    await Promise.resolve();
    expect(ctx.shell.isModalOpen('health')).toBe(false);
    expect(document.querySelector('#overview-status')?.textContent).toBe('正在加载健康数据…');
    controller.stop?.();
  });

  it('owns daily, range, sleep and import request events', async () => {
    const ctx = context();
    const deps = dependencies();
    const controller = createHealthController(ctx, deps);
    controller.start();
    await Promise.all([
      new Promise((resolve) => ctx.events.emit('health:daily', { day: '2026-09-14', resolve })),
      new Promise((resolve) =>
        ctx.events.emit('health:range', {
          params: 'start_date=2026-09-01&end_date=2026-09-14',
          resolve,
        }),
      ),
      new Promise((resolve) => ctx.events.emit('health:sleep', { day: '2026-09-14', resolve })),
      new Promise((resolve) => ctx.events.emit('health:raw-audit', { resolve })),
      new Promise((resolve) =>
        ctx.events.emit('health:import-details', { id: 'import-1', resolve }),
      ),
    ]);
    expect(deps.api.daily).toHaveBeenCalled();
    expect(deps.api.range).toHaveBeenCalled();
    expect(deps.api.sleep).toHaveBeenCalled();
    expect(deps.api.rawAudit).toHaveBeenCalled();
    expect(deps.api.importDetails).toHaveBeenCalled();
    controller.stop?.();
  });
});
