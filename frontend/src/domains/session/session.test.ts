import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAppState } from '../../app/app-state';
import { createEventBus } from '../../app/events';
import { createShell } from '../../app/shell';
import { createOperationRegistry } from '../../shared/operations';
import type { DomainContext } from '../domain-factory';
import {
  createSessionController,
  type SessionControllerDependencies,
  validateSessionGarminHours,
} from './controller';
import { sessionState } from './state';

function mountPage(): void {
  document.body.innerHTML = `
    <div id="chat"><div id="hint"></div></div>
    <textarea id="message"></textarea><button id="send">发送</button>
    <button id="food-image-button">图片</button>
    <button id="btn-logout"><span class="lbl">退出</span></button>
    <button id="btn-llm-test">LLM 测试</button>
    <div id="ended-overlay"></div>
    <input id="session-garmin-hours" value="0">
    <button id="session-recovery-apply">应用</button>
    <span id="session-recovery-status"></span>
    <input id="external-models-toggle" type="checkbox">
    <div id="external-models-status"></div>
    <div id="external-model-disclosure"></div>
    <div id="external-model-local-features"></div>
    <form id="runtime-settings-form">
      <input name="agent_max_steps"><input name="llm_temperature">
      <input name="llm_max_tokens"><input name="llm_timeout_seconds">
      <input name="llm_max_retries"><input name="chat_timeout_seconds">
      <button id="runtime-settings-save"></button>
    </form>`;
}

function createDependencies(): SessionControllerDependencies {
  return {
    session: {
      intro: vi.fn().mockResolvedValue({
        message: '恢复优先',
        garmin_recovery_hours: 0,
        soreness_prompt_regions: ['back'],
      }),
      logout: vi.fn().mockResolvedValue({ status: 'no_messages' }),
    },
    settings: {
      externalModels: vi.fn().mockResolvedValue({
        external_models_enabled: false,
        disclosure: [{ name: '模型', data: '对话' }],
        local_features: ['训练编辑'],
      }),
      updateExternalModels: vi
        .fn()
        .mockImplementation((enabled: boolean) =>
          Promise.resolve({ external_models_enabled: enabled }),
        ),
      runtime: vi.fn().mockResolvedValue({
        agent_max_steps: 15,
        llm_temperature: 0.7,
        llm_max_tokens: null,
        llm_timeout_seconds: 90,
        llm_max_retries: 0,
        chat_timeout_seconds: 600,
      }),
      updateRuntime: vi.fn().mockImplementation((values) => Promise.resolve(values)),
      testLlm: vi.fn().mockResolvedValue({ ok: true, model: 'test', latency_ms: 12 }),
      profileStatus: vi.fn().mockResolvedValue({
        complete: false,
        missing_fields: ['height_cm'],
        prompt: '请补全档案',
      }),
    },
    health: {
      storageStatus: vi.fn().mockResolvedValue({ available: false, message: '存储降级' }),
    },
  } as unknown as SessionControllerDependencies;
}

function createContext(): DomainContext {
  return {
    state: createAppState(),
    events: createEventBus(),
    shell: createShell(),
    operations: createOperationRegistry(),
    document,
  };
}

beforeEach(() => {
  mountPage();
  Object.assign(sessionState, {
    status: 'idle',
    message: undefined,
    ended: false,
    externalModelsEnabled: true,
    garminRecoveryHours: 0,
    sorenessPromptRegions: [],
    conversationHistory: [],
  });
});

describe('session domain', () => {
  it('loads intro, privacy settings, profile and storage through typed adapters', async () => {
    const context = createContext();
    const dependencies = createDependencies();
    const controller = createSessionController(context, dependencies);
    controller.start();
    context.events.emit('startup');

    await vi.waitFor(() => expect(sessionState.status).toBe('ready'));
    expect(dependencies.session.intro).toHaveBeenCalledWith(0, expect.any(AbortSignal));
    expect(dependencies.settings.externalModels).toHaveBeenCalledOnce();
    expect(dependencies.settings.runtime).toHaveBeenCalledOnce();
    expect(dependencies.settings.profileStatus).toHaveBeenCalledOnce();
    expect(dependencies.health.storageStatus).toHaveBeenCalledOnce();
    expect(document.querySelector('.session-intro')?.textContent).toContain('恢复优先');
    expect(document.querySelector('#external-models-status')?.textContent).toContain('已关闭');
    expect(document.body.textContent).toContain('还缺：身高');
    expect(document.body.textContent).toContain('存储降级');
    controller.stop?.();
  });

  it('validates and refreshes Garmin recovery time', async () => {
    expect(validateSessionGarminHours('12.34')).toBe(12.3);
    expect(() => validateSessionGarminHours('97')).toThrow(/0–96/);
    const context = createContext();
    const dependencies = createDependencies();
    const controller = createSessionController(context, dependencies);
    controller.start();

    const input = document.querySelector<HTMLInputElement>('#session-garmin-hours')!;
    input.value = '18.5';
    document.querySelector<HTMLButtonElement>('#session-recovery-apply')!.click();
    await vi.waitFor(() =>
      expect(dependencies.session.intro).toHaveBeenCalledWith(18.5, expect.any(AbortSignal)),
    );
    controller.stop?.();
  });

  it('updates privacy and reports LLM connectivity without direct fetch', async () => {
    const context = createContext();
    const dependencies = createDependencies();
    const controller = createSessionController(context, dependencies);
    controller.start();

    const toggle = document.querySelector<HTMLInputElement>('#external-models-toggle')!;
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change'));
    document.querySelector<HTMLButtonElement>('#btn-llm-test')!.click();

    await vi.waitFor(() => expect(dependencies.settings.updateExternalModels).toHaveBeenCalled());
    await vi.waitFor(() => expect(document.body.textContent).toContain('LLM 连通性正常'));
    expect(sessionState.externalModelsEnabled).toBe(false);
    controller.stop?.();
  });

  it('saves all runtime parameters from settings', async () => {
    const context = createContext();
    const dependencies = createDependencies();
    const controller = createSessionController(context, dependencies);
    controller.start();
    const values: Record<string, string> = {
      agent_max_steps: '20',
      llm_temperature: '0.3',
      llm_max_tokens: '4096',
      llm_timeout_seconds: '120',
      llm_max_retries: '2',
      chat_timeout_seconds: '900',
    };
    for (const [name, value] of Object.entries(values)) {
      document.querySelector<HTMLInputElement>(`[name="${name}"]`)!.value = value;
    }
    document.querySelector<HTMLFormElement>('#runtime-settings-form')!.requestSubmit();
    await vi.waitFor(() => expect(dependencies.settings.updateRuntime).toHaveBeenCalled());
    expect(dependencies.settings.updateRuntime).toHaveBeenCalledWith(
      {
        agent_max_steps: 20,
        llm_temperature: 0.3,
        llm_max_tokens: 4096,
        llm_timeout_seconds: 120,
        llm_max_retries: 2,
        chat_timeout_seconds: 900,
      },
      expect.any(AbortSignal),
    );
    controller.stop?.();
  });

  it('sends the bounded conversation as messages and ends once', async () => {
    const context = createContext();
    const dependencies = createDependencies();
    dependencies.session.logout = vi.fn().mockResolvedValue({
      saved: true,
      user_confirmed: true,
      summary: '摘要',
    });
    sessionState.conversationHistory.push({ role: 'user', text: '你好' });
    const controller = createSessionController(context, dependencies);
    controller.start();
    controller.start();

    const button = document.querySelector<HTMLButtonElement>('#btn-logout')!;
    button.click();
    button.click();
    await vi.waitFor(() => expect(sessionState.ended).toBe(true));
    expect(dependencies.session.logout).toHaveBeenCalledTimes(1);
    expect(dependencies.session.logout).toHaveBeenCalledWith(
      [{ role: 'user', text: '你好' }],
      expect.any(AbortSignal),
    );
    expect(document.querySelector('#ended-overlay')?.classList.contains('show')).toBe(true);
    controller.stop?.();
  });

  it('removes DOM listeners and cancels operations on stop', () => {
    const context = createContext();
    const dependencies = createDependencies();
    const controller = createSessionController(context, dependencies);
    controller.start();
    controller.stop?.();
    document.querySelector<HTMLButtonElement>('#btn-llm-test')!.click();
    expect(dependencies.settings.testLlm).not.toHaveBeenCalled();
  });

  it('treats a 403 connectivity result as an informational privacy outcome', async () => {
    const context = createContext();
    const dependencies = createDependencies();
    const forbidden = Object.assign(new Error('外部模型已关闭'), { status: 403 });
    dependencies.settings.testLlm = vi.fn().mockRejectedValue(forbidden);
    const controller = createSessionController(context, dependencies);
    controller.start();
    document.querySelector<HTMLButtonElement>('#btn-llm-test')!.click();
    await vi.waitFor(() => expect(document.querySelector('.notify-skipped')).not.toBeNull());
    expect(document.querySelector('.notify-error')).toBeNull();
    controller.stop?.();
  });

  it('does not render a startup response after the controller is stopped', async () => {
    let resolveIntro: ((value: { message: string }) => void) | undefined;
    const context = createContext();
    const dependencies = createDependencies();
    dependencies.session.intro = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveIntro = resolve;
      }),
    );
    const controller = createSessionController(context, dependencies);
    controller.start();
    context.events.emit('startup');
    controller.stop?.();
    resolveIntro?.({ message: '不应渲染' });
    await Promise.resolve();
    await Promise.resolve();
    expect(document.body.textContent).not.toContain('不应渲染');
  });
});
