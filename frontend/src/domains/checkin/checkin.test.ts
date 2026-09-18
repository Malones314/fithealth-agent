import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createCheckinController, type CheckinDependencies } from './controller';

function mount(): void {
  document.body.innerHTML = `<div id="chat"></div><button id="btn-checkin"></button><button id="open-checkin"></button><div id="checkin-modal" aria-hidden="true"><div role="dialog"><button id="checkin-modal-close"></button><form id="checkin-form"><input id="checkin-date" name="date" type="date"><button id="add-nutrition-record" type="button"></button><div id="checkin-hint"></div><input name="weight_kg"><input name="sleep_quality"><input name="energy_level"><input name="fatigue_level"><input name="pain_level"><input name="calories_kcal"><input name="protein_g"><input name="carbs_g"><input name="fat_g"><input name="training_rpe"><input name="training_completion_pct"><input name="cheat_meal" type="checkbox"><div id="meal-estimate-section"></div><div id="meal-estimates"></div><textarea name="note"></textarea><button id="checkin-cancel" type="button"></button><button type="submit">保存</button></form></div></div>`;
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
function dependencies(): CheckinDependencies {
  return {
    api: {
      checkin: vi.fn().mockResolvedValue({ checkin: null }),
      saveCheckin: vi.fn().mockResolvedValue({ created: true, date: '2026-09-14' }),
    },
    prompt: vi.fn().mockReturnValue('晚餐'),
  } as unknown as CheckinDependencies;
}
beforeEach(mount);

describe('checkin domain', () => {
  it('loads an existing record when opened and preserves cleared loaded fields', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.checkin = vi
      .fn()
      .mockResolvedValue({ checkin: { date: '2026-09-14', weight_kg: 70, note: '恢复良好' } });
    const controller = createCheckinController(ctx, deps);
    controller.start();
    ctx.events.emit('checkin:open', { day: '2026-09-14' });
    await vi.waitFor(() =>
      expect(document.querySelector<HTMLInputElement>('[name="weight_kg"]')?.value).toBe('70'),
    );
    document.querySelector<HTMLInputElement>('[name="weight_kg"]')!.value = '';
    document.querySelector<HTMLFormElement>('#checkin-form')!.requestSubmit();
    await vi.waitFor(() => expect(deps.api.saveCheckin).toHaveBeenCalled());
    expect(deps.api.saveCheckin).toHaveBeenCalledWith(
      expect.objectContaining({ weight_kg: null, note: '恢复良好' }),
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('keeps only the newest day when date responses arrive out of order', async () => {
    const ctx = context();
    const deps = dependencies();
    let old: ((value: unknown) => void) | undefined;
    deps.api.checkin = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            old = resolve;
          }),
      )
      .mockResolvedValueOnce({ checkin: { date: '2026-09-14', weight_kg: 72 } });
    const controller = createCheckinController(ctx, deps);
    controller.start();
    ctx.events.emit('checkin:open', { day: '2026-09-13' });
    const date = document.querySelector<HTMLInputElement>('#checkin-date')!;
    date.value = '2026-09-14';
    date.dispatchEvent(new Event('change'));
    await vi.waitFor(() =>
      expect(document.querySelector<HTMLInputElement>('[name="weight_kg"]')?.value).toBe('72'),
    );
    old?.({ checkin: { date: '2026-09-13', weight_kg: 60 } });
    await Promise.resolve();
    expect(document.querySelector<HTMLInputElement>('[name="weight_kg"]')?.value).toBe('72');
    controller.stop?.();
  });

  it('turns a food event into an editable meal and applies nutrient totals', async () => {
    const ctx = context();
    const controller = createCheckinController(ctx, dependencies());
    controller.start();
    ctx.events.emit('checkin:food', {
      items: [
        { name: '米饭', portion: '1碗', calories_kcal: 300, protein_g: 6, carbs_g: 65, fat_g: 1 },
      ],
      confidence: 'high',
    });
    await vi.waitFor(() =>
      expect(
        document.querySelector<HTMLInputElement>('#meal-estimates input[aria-label="食物"]')?.value,
      ).toBe('米饭'),
    );
    expect(document.querySelector<HTMLInputElement>('[name="calories_kcal"]')?.value).toBe('300');
    controller.stop?.();
  });

  it('maps 422 errors to the field and clears validity when corrected', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.saveCheckin = vi.fn().mockRejectedValue(
      Object.assign(new Error('字段错误'), {
        status: 422,
        details: { field_errors: { weight_kg: '体重超出范围' } },
      }),
    );
    const controller = createCheckinController(ctx, deps);
    controller.start();
    ctx.events.emit('checkin:open', { day: '2026-09-14' });
    await vi.waitFor(() => expect(deps.api.checkin).toHaveBeenCalled());
    const weight = document.querySelector<HTMLInputElement>('[name="weight_kg"]')!;
    weight.value = '999';
    document.querySelector<HTMLFormElement>('#checkin-form')!.requestSubmit();
    await vi.waitFor(() => expect(weight.validationMessage).toBe('体重超出范围'));
    weight.value = '70';
    weight.dispatchEvent(new Event('input', { bubbles: true }));
    expect(weight.validationMessage).toBe('');
    expect(ctx.shell.isModalOpen('checkin')).toBe(true);
    controller.stop?.();
  });

  it('cancels in-flight work and removes listeners on stop', () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.checkin = vi.fn().mockReturnValue(new Promise(() => undefined));
    const controller = createCheckinController(ctx, deps);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-checkin')!.click();
    controller.stop?.();
    expect(ctx.operations.pending()).toEqual([]);
    document.querySelector<HTMLButtonElement>('#btn-checkin')!.click();
    expect(deps.api.checkin).toHaveBeenCalledTimes(1);
  });
});
