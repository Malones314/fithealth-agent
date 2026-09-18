import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createWorkoutController, type WorkoutDependencies } from './controller';
import { createWorkoutState, loadWorkoutSnapshot } from './state';

function mountPage(): void {
  document.body.innerHTML = `
    <div id="chat"></div><section id="training-editor-area">
    <div id="workout-list"></div><span id="set-count"></span><div id="workout-actions"></div>
    <span id="workout-time"></span><div id="workout-note-wrap"><textarea id="workout-note"></textarea></div>
    <div id="editor-toolbar"><span id="editor-selection"></span>
    <button id="btn-merge"></button><button id="btn-rename"></button><button id="btn-undo"></button>
    <button id="btn-restore-source"></button><button id="btn-clear"></button></div>
    <button id="btn-confirm"></button><button id="btn-discard"></button></section>`;
}

function context(): DomainContext {
  const shell = createShell();
  return {
    state: createAppState(),
    events: createEventBus(),
    shell: { ...shell, ask: () => true },
    operations: createOperationRegistry(),
    document,
  };
}

function dependencies(): WorkoutDependencies {
  return {
    api: {
      state: vi.fn().mockResolvedValue({ has_workout: false }),
      update: vi.fn().mockResolvedValue({}),
      quarantined: vi.fn().mockResolvedValue({ files: [] }),
      preview: vi.fn(),
      save: vi.fn(),
      savedRecord: vi.fn().mockResolvedValue({
        record: {
          name: '历史训练',
          segments: [{ index: 1, segment_type: 'set_active', weight_kg: 20, repetitions: 8 }],
        },
        revision: 3,
      }),
      updateSavedRecord: vi.fn().mockResolvedValue({ revision: 4 }),
    },
  } as unknown as WorkoutDependencies;
}

beforeEach(() => mountPage());

describe('workout domain', () => {
  it('keeps the server snapshot separate from the editable draft', () => {
    const state = createWorkoutState();
    const source = { name: '力量', sets: [{ index: 1, weight_kg: 20 }] };
    loadWorkoutSnapshot(state, source);
    state.draft!.sets![0].weight_kg = 30;
    expect(state.snapshot!.sets![0].weight_kg).toBe(20);
    expect(source.sets[0].weight_kg).toBe(20);
  });

  it('loads pending state through the adapter and renders cards', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: { sets: [{ index: 1, segment_type: 'set_active', category: '深蹲' }] },
      confirmation: { workout_id: 'w', version: 1, confirmation_token: 't' },
    });
    const statuses: unknown[] = [];
    ctx.events.on('workout:status', (value) => statuses.push(value));
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(document.querySelectorAll('.set-card')).toHaveLength(1));
    expect(deps.api.state).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(statuses).toContainEqual({ hasWorkout: true, mode: 'pending' });
    controller.stop?.();
  });

  it('renders editor fields with the layout classes and only selects active sets', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: {
        sets: [
          { index: 1, segment_type: 'set_active', category: '深蹲' },
          { index: 2, segment_type: 'set_rest', is_rest: true, duration_s: 30 },
        ],
      },
      confirmation: { workout_id: 'w', version: 1, confirmation_token: 't' },
    });
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(document.querySelectorAll('.set-card')).toHaveLength(2));
    expect(document.querySelector('.editor-field')).not.toBeNull();
    expect(document.querySelector('.editor-input')).not.toBeNull();
    expect(document.querySelectorAll('.set-card input[type="checkbox"]')).toHaveLength(1);
    controller.stop?.();
  });

  it('submits only active sets when a pending workout contains rest segments', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: {
        sets: [
          { index: 1, segment_type: 'set_active', category: '深蹲', weight_kg: 20, repetitions: 8 },
          { index: 2, segment_type: 'set_rest', is_rest: true, duration_s: 30 },
        ],
      },
      confirmation: { workout_id: 'w', version: 1, confirmation_token: 't' },
    });
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(document.querySelectorAll('.set-card')).toHaveLength(2));
    document.querySelector<HTMLButtonElement>('#btn-confirm')!.click();
    await vi.waitFor(() =>
      expect(deps.api.update).toHaveBeenCalledWith(
        'confirm_with_updates',
        expect.objectContaining({ updates: [expect.objectContaining({ index: 1 })] }),
        expect.any(AbortSignal),
      ),
    );
    controller.stop?.();
  });

  it('clears the selection after renaming pending sets', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: { sets: [{ index: 1, segment_type: 'set_active', category: '深蹲' }] },
      confirmation: { workout_id: 'w', version: 1, confirmation_token: 't' },
    });
    vi.spyOn(window, 'prompt').mockReturnValue('杠铃深蹲');
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(document.querySelector('.set-card')).not.toBeNull());
    const checkbox = document.querySelector<HTMLInputElement>('.set-card input[type="checkbox"]')!;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    document.querySelector<HTMLButtonElement>('#btn-rename')!.click();
    await vi.waitFor(() => expect(deps.api.state).toHaveBeenCalledTimes(2));
    expect(document.querySelector('#editor-selection')?.textContent).toBe('已选择 0 组');
    expect(
      document.querySelector<HTMLInputElement>('.set-card input[type="checkbox"]')?.checked,
    ).toBe(false);
    controller.stop?.();
  });

  it('does not schedule background polling that can overwrite editor state', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: { sets: [{ index: 1, segment_type: 'set_active', weight_kg: 20 }] },
    });
    const interval = vi.spyOn(globalThis, 'setInterval');
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() =>
      expect(document.querySelector<HTMLInputElement>('.set-editor input')).not.toBeNull(),
    );
    const checkbox = document.querySelector<HTMLInputElement>('.set-select')!;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    expect(deps.api.state).toHaveBeenCalledTimes(1);
    expect(checkbox.checked).toBe(true);
    expect(document.querySelector('#editor-selection')?.textContent).toBe('已选择 1 组');
    expect(interval).not.toHaveBeenCalled();
    interval.mockRestore();
    controller.stop?.();
  });

  it('routes undo and source restore controls through the pending-workout adapter', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({
      has_workout: true,
      workout: { sets: [] },
      edit_history: { can_undo: true, can_restore_parsed_source: true },
    });
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() =>
      expect(document.querySelector<HTMLButtonElement>('#btn-undo')!.disabled).toBe(false),
    );
    document.querySelector<HTMLButtonElement>('#btn-undo')!.click();
    document.querySelector<HTMLButtonElement>('#btn-restore-source')!.click();
    await vi.waitFor(() => expect(deps.api.update).toHaveBeenCalledTimes(2));
    expect(deps.api.update).toHaveBeenCalledWith('undo_last_edit', {}, expect.any(AbortSignal));
    expect(deps.api.update).toHaveBeenCalledWith(
      'restore_parsed_source',
      {},
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('reports quarantined workouts at startup without a blocking confirmation', async () => {
    const ctx = context();
    const deps = dependencies();
    const ask = vi.spyOn(ctx.shell, 'ask');
    deps.api.quarantined = vi
      .fn()
      .mockResolvedValue({ files: [{ recoverable: true, segments: 1 }] });
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(deps.api.quarantined).toHaveBeenCalled());
    expect(ask).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain('可以恢复');
    controller.stop?.();
  });

  it('loads and saves a historical record with its revision', async () => {
    const ctx = context();
    const deps = dependencies();
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('workout:edit-saved', { recordId: 'rec/1', showNotice: false });
    await vi.waitFor(() =>
      expect(document.querySelector('#btn-confirm')?.textContent).toContain('修改'),
    );
    document.querySelector<HTMLButtonElement>('#btn-confirm')!.click();
    await vi.waitFor(() => expect(deps.api.updateSavedRecord).toHaveBeenCalled());
    expect(deps.api.updateSavedRecord).toHaveBeenCalledWith(
      'rec/1',
      3,
      expect.objectContaining({ updates: expect.any(Array) }),
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('sends saved-record merge metadata through the revisioned PATCH', async () => {
    const ctx = context();
    const deps = dependencies();
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    deps.api.savedRecord = vi.fn().mockResolvedValue({
      record: {
        segments: [
          { index: 1, segment_type: 'set_active' },
          { index: 2, segment_type: 'set_active' },
        ],
      },
      revision: 5,
    });
    ctx.events.emit('workout:edit-saved', { recordId: 'rec-merge' });
    await vi.waitFor(() => expect(document.querySelectorAll('.set-card')).toHaveLength(2));
    document
      .querySelectorAll<HTMLInputElement>('.set-card input[type="checkbox"]')
      .forEach((input) => {
        input.checked = true;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    document.querySelector<HTMLButtonElement>('#btn-merge')!.click();
    await vi.waitFor(() => expect(deps.api.updateSavedRecord).toHaveBeenCalled());
    expect(deps.api.updateSavedRecord).toHaveBeenCalledWith(
      'rec-merge',
      5,
      expect.objectContaining({ merge_indices: [1, 2] }),
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('preserves the historical draft and exposes conflict state on 409', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.updateSavedRecord = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('revision stale'), { status: 409 }));
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('workout:edit-saved', { recordId: 'rec-1' });
    await vi.waitFor(() =>
      expect(document.querySelector<HTMLInputElement>('.set-editor input')).not.toBeNull(),
    );
    const input = document.querySelector<HTMLInputElement>('.set-editor input')!;
    input.value = '42';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector<HTMLButtonElement>('#btn-confirm')!.click();
    await vi.waitFor(() => expect(document.documentElement.dataset.workoutStatus).toBe('conflict'));
    expect(document.querySelector<HTMLInputElement>('.set-editor input')!.value).toBe('42');
    controller.stop?.();
  });

  it('cancels pending workout and saved-record requests on stop', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.state = vi.fn().mockResolvedValue({ has_workout: true, workout: { sets: [] } });
    const controller = createWorkoutController(ctx, deps);
    controller.start();
    ctx.events.emit('startup');
    await vi.waitFor(() => expect(deps.api.state).toHaveBeenCalled());
    controller.stop?.();
    expect(ctx.operations.pending()).toEqual([]);
  });
});
