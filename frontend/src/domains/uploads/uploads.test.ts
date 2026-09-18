import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createUploadsController, type UploadDependencies } from './controller';
import { activityKey } from './state';

function mountPage(): void {
  document.body.innerHTML = `<div id="chat"></div><div class="chat-panel"></div>
    <input id="file-input" type="file"><button id="file-upload-button"></button>
    <input id="food-image-input" type="file"><button id="food-image-button"></button>
    <div id="food-attachment"><span id="food-attachment-name"></span><button id="food-attachment-remove"></button></div>
    <div id="activity-picker-modal" aria-hidden="true"><div role="dialog"><button id="activity-picker-close"></button><div id="activity-picker-list"></div></div></div>`;
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

function dependencies(): UploadDependencies {
  return {
    api: {
      fit: vi.fn().mockResolvedValue({ status: 'ok', workout: { sets: [] } }),
      plan: vi
        .fn()
        .mockResolvedValue({ status: 'ok', valid: true, content: 'plan', subject: '力量' }),
      health: vi.fn(),
      healthBatch: vi.fn().mockResolvedValue({ status: 'ok' }),
      healthActivity: vi.fn().mockResolvedValue({ workout: { sets: [] } }),
      food: vi.fn().mockResolvedValue({ status: 'ok' }),
    },
  } as unknown as UploadDependencies;
}

function choose(files: File[]): void {
  const input = document.querySelector<HTMLInputElement>('#file-input')!;
  Object.defineProperty(input, 'files', { configurable: true, value: files });
  input.dispatchEvent(new Event('change'));
}

beforeEach(() => {
  mountPage();
  delete (globalThis as { openActivityPicker?: unknown }).openActivityPicker;
});

describe('uploads domain', () => {
  it('uploads FIT through the adapter and publishes the workout', async () => {
    const ctx = context();
    const deps = dependencies();
    const loaded = vi.fn();
    ctx.events.on('workout:loaded', loaded);
    const controller = createUploadsController(ctx, deps);
    controller.start();
    choose([new File(['fit'], 'lift.fit')]);
    await vi.waitFor(() => expect(loaded).toHaveBeenCalledWith({ sets: [] }));
    expect(deps.api.fit).toHaveBeenCalledWith(expect.any(File), false, expect.any(AbortSignal));
    controller.stop?.();
  });

  it('keeps same-name activities from different ZIP files distinct', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.healthBatch = vi.fn().mockResolvedValue({
      status: 'ok',
      activities: [
        { zip: 'a.zip', name: 'ride.fit' },
        { zip: 'b.zip', name: 'ride.fit' },
      ],
    });
    const controller = createUploadsController(ctx, deps);
    controller.start();
    choose([new File(['a'], 'a.zip'), new File(['b'], 'b.zip')]);
    await vi.waitFor(() =>
      expect(document.querySelectorAll('#activity-picker-list button')).toHaveLength(2),
    );
    expect(
      Array.from(document.querySelectorAll('#activity-picker-list span')).map(
        (node) => node.textContent,
      ),
    ).toEqual(['a.zip › ride.fit', 'b.zip › ride.fit']);
    expect(activityKey({ zip: 'a.zip', name: 'ride.fit' })).not.toBe(
      activityKey({ zip: 'b.zip', name: 'ride.fit' }),
    );
    document.querySelector<HTMLButtonElement>('#activity-picker-list button')!.click();
    await vi.waitFor(() => expect(deps.api.healthActivity).toHaveBeenCalled());
    expect(deps.api.healthActivity).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'a.zip' }),
      'a.zip',
      'ride.fit',
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('rejects invalid types and oversized files before calling an API', async () => {
    const ctx = context();
    const deps = dependencies();
    const controller = createUploadsController(ctx, deps);
    controller.start();
    choose([new File(['x'], 'bad.exe')]);
    await vi.waitFor(() => expect(document.documentElement.dataset.uploadActive).toBe('false'));
    expect(deps.api.fit).not.toHaveBeenCalled();
    expect(deps.api.plan).not.toHaveBeenCalled();
    controller.stop?.();
  });

  it('guards against duplicate uploads while one is in flight', async () => {
    let resolve: ((value: { status: string }) => void) | undefined;
    const ctx = context();
    const deps = dependencies();
    deps.api.fit = vi.fn().mockReturnValue(
      new Promise((value) => {
        resolve = value;
      }),
    );
    const controller = createUploadsController(ctx, deps);
    controller.start();
    const file = new File(['fit'], 'lift.fit');
    choose([file]);
    choose([file]);
    expect(deps.api.fit).toHaveBeenCalledTimes(1);
    resolve?.({ status: 'ok' });
    await Promise.resolve();
    controller.stop?.();
  });

  it('requires confirmation before replacing a pending workout', async () => {
    const ctx = context();
    const deps = dependencies();
    const ask = vi.spyOn(ctx.shell, 'ask').mockReturnValue(true);
    const controller = createUploadsController(ctx, deps);
    controller.start();
    ctx.events.emit('workout:status', { hasWorkout: true, mode: 'pending' });
    choose([new File(['fit'], 'replacement.fit')]);
    await vi.waitFor(() => expect(deps.api.fit).toHaveBeenCalled());
    expect(ask).toHaveBeenCalledWith('覆盖当前待确认训练吗？', expect.any(String));
    expect(deps.api.fit).toHaveBeenCalledWith(expect.any(File), true, expect.any(AbortSignal));
    controller.stop?.();
  });

  it('cancels activity loading when the picker closes', async () => {
    const ctx = context();
    const deps = dependencies();
    deps.api.healthBatch = vi
      .fn()
      .mockResolvedValue({ status: 'ok', activities: [{ zip: 'a.zip', name: 'ride.fit' }] });
    deps.api.healthActivity = vi.fn().mockReturnValue(new Promise(() => undefined));
    const controller = createUploadsController(ctx, deps);
    controller.start();
    choose([new File(['a'], 'a.zip')]);
    await vi.waitFor(() =>
      expect(document.querySelector('#activity-picker-list button')).not.toBeNull(),
    );
    document.querySelector<HTMLButtonElement>('#activity-picker-list button')!.click();
    document.querySelector<HTMLButtonElement>('#activity-picker-close')!.click();
    expect(ctx.operations.pending()).not.toContain('upload-health-activity');
    expect(document.documentElement.dataset.uploadActive).toBe('false');
    controller.stop?.();
  });

  it('does not install a global activity picker compatibility hook', () => {
    const ctx = context();
    const controller = createUploadsController(ctx, dependencies());
    controller.start();
    expect((globalThis as { openActivityPicker?: unknown }).openActivityPicker).toBeUndefined();
    controller.stop?.();
  });
});
