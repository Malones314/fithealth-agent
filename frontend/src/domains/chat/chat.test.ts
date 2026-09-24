import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import { createChatController, type ChatDependencies } from './controller';
function mount() {
  document.body.innerHTML =
    '<div id="chat"><div id="hint"></div></div><form id="form"><div id="message-resizer"></div><textarea id="message"></textarea><button id="send"></button></form>';
}
function context(): DomainContext {
  return {
    state: createAppState({
      ended: false,
      externalModelsEnabled: true,
      garminRecoveryHours: 12,
      sorenessPromptRegions: ['back'],
      conversationHistory: [{ role: 'user', text: '之前' }],
    }),
    events: createEventBus(),
    shell: createShell(),
    operations: createOperationRegistry(),
    document,
  };
}
function deps(): ChatDependencies {
  return {
    chat: { send: vi.fn().mockResolvedValue({ reply: '收到' }) },
    plans: { create: vi.fn().mockResolvedValue({ filename: 'plan.md' }) },
    settings: { confirmProfileUpdate: vi.fn().mockResolvedValue({}) },
  } as unknown as ChatDependencies;
}
beforeEach(() => {
  localStorage.removeItem('fithealth-message-height-v1');
  mount();
});
describe('chat domain', () => {
  it('resizes the message input by pointer and keyboard and persists the height', () => {
    const ctx = context();
    const api = deps();
    const input = document.querySelector<HTMLTextAreaElement>('#message')!;
    vi.spyOn(input, 'getBoundingClientRect').mockImplementation(
      () =>
        ({
          height:
            Number.parseFloat(
              document
                .querySelector<HTMLFormElement>('#form')!
                .style.getPropertyValue('--message-height'),
            ) || 72,
        }) as DOMRect,
    );
    const resizer = document.querySelector<HTMLElement>('#message-resizer')!;
    Object.assign(resizer, {
      setPointerCapture: vi.fn(),
      hasPointerCapture: vi.fn().mockReturnValue(true),
      releasePointerCapture: vi.fn(),
    });
    const controller = createChatController(ctx, api);
    controller.start();
    const pointer = (type: string, y: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        clientY: { value: y },
        pointerId: { value: 7 },
      });
      resizer.dispatchEvent(event);
    };
    pointer('pointerdown', 200);
    pointer('pointermove', 120);
    pointer('pointerup', 120);
    expect(
      document.querySelector<HTMLFormElement>('#form')!.style.getPropertyValue('--message-height'),
    ).toBe('152px');
    expect(localStorage.getItem('fithealth-message-height-v1')).toBe('152');
    resizer.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(
      document.querySelector<HTMLFormElement>('#form')!.style.getPropertyValue('--message-height'),
    ).toBe('136px');
    expect(resizer.getAttribute('aria-valuenow')).toBe('136');
    controller.stop?.();
  });
  it('sends bounded context through the adapter and updates conversation memory', async () => {
    const ctx = context();
    const api = deps();
    const controller = createChatController(ctx, api);
    controller.start();
    const input = document.querySelector<HTMLTextAreaElement>('#message')!;
    input.value = '今天深蹲';
    document.querySelector<HTMLFormElement>('#form')!.requestSubmit();
    await vi.waitFor(() => expect(api.chat.send).toHaveBeenCalled());
    expect(api.chat.send).toHaveBeenCalledWith(
      expect.objectContaining({
        message: '今天深蹲',
        history: [{ role: 'user', content: '之前' }],
        garmin_recovery_hours: 12,
        soreness_prompt_regions: ['back'],
      }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    await vi.waitFor(() => expect(document.querySelector('#chat')?.textContent).toContain('收到'));
    expect(ctx.state.sessionContext.conversationHistory.at(-1)?.text).toBe('收到');
    controller.stop?.();
  });
  it('preserves artifact draft_id when saving a generated plan', async () => {
    const ctx = context();
    const api = deps();
    api.chat.send = vi.fn().mockResolvedValue({
      reply: '计划',
      artifact: {
        type: 'training_plan',
        content: '正文',
        draft_id: 'draft-7',
        suggested_date: '2026-09-14',
      },
    });
    const controller = createChatController(ctx, api);
    controller.start();
    document.querySelector<HTMLTextAreaElement>('#message')!.value = '生成计划';
    document.querySelector<HTMLFormElement>('#form')!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector('[data-plan-save]')).not.toBeNull());
    document.querySelector<HTMLButtonElement>('[data-plan-save]')!.click();
    await vi.waitFor(() => expect(api.plans.create).toHaveBeenCalled());
    expect(api.plans.create).toHaveBeenCalledWith(
      expect.objectContaining({ draft_id: 'draft-7', content: '正文' }),
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });
  it('lays out plan metadata together and downloads the generated markdown', async () => {
    const ctx = context();
    const api = deps();
    api.chat.send = vi.fn().mockResolvedValue({
      reply: '计划',
      artifact: {
        type: 'training_plan',
        content: '# 今日肩部训练',
        suggested_date: '2026-09-15',
        subject: '肩部',
        title: '今日肩部训练',
      },
    });
    const createObjectURL = vi.fn().mockReturnValue('blob:plan');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    const controller = createChatController(ctx, api);
    controller.start();
    document.querySelector<HTMLTextAreaElement>('#message')!.value = '生成计划';
    document.querySelector<HTMLFormElement>('#form')!.requestSubmit();
    await vi.waitFor(() => expect(document.querySelector('[data-plan-download]')).not.toBeNull());
    expect(document.querySelectorAll('.plan-save-grid > .plan-save-field')).toHaveLength(4);
    expect(document.querySelector('.plan-save-grid > .plan-save-field.full')).not.toBeNull();
    document.querySelector<HTMLButtonElement>('[data-plan-download]')!.click();
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:plan');
    controller.stop?.();
    vi.unstubAllGlobals();
  });
  it('renders profile equipment additions and removals before confirmation', async () => {
    const ctx = context();
    const api = deps();
    api.chat.send = vi.fn().mockResolvedValue({
      reply: '请确认',
      artifact: {
        type: 'profile_update',
        summary: '器械变化',
        equipment_diff: { added: ['壶铃'], removed: ['史密斯机'] },
      },
    });
    const controller = createChatController(ctx, api);
    controller.start();
    document.querySelector<HTMLTextAreaElement>('#message')!.value = '更新器械';
    document.querySelector<HTMLFormElement>('#form')!.requestSubmit();
    await vi.waitFor(() =>
      expect(document.querySelector('.profile-update-panel')?.textContent).toContain(
        '新增器械：壶铃',
      ),
    );
    expect(document.querySelector('.profile-update-panel')?.textContent).toContain(
      '移除器械：史密斯机',
    );
    controller.stop?.();
  });
  it('renders weekly validation state and cancels in-flight sends on stop', async () => {
    const ctx = context();
    const api = deps();
    api.chat.send = vi.fn().mockReturnValue(new Promise(() => undefined));
    const controller = createChatController(ctx, api);
    controller.start();
    document.querySelector<HTMLTextAreaElement>('#message')!.value = '请求';
    document.querySelector<HTMLFormElement>('#form')!.requestSubmit();
    controller.stop?.();
    expect(ctx.operations.pending()).toEqual([]);
    expect(document.documentElement.dataset.chatPending).toBe('true');
  });
});
