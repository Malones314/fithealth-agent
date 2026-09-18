import type { ExternalModelSettings, RuntimeSettings, SessionState } from './types';
import type { SessionIntroResponse } from '../../api/session';

export interface SessionViewHandlers {
  onRecoveryApply(value: string): void;
  onExternalModelsChange(enabled: boolean): void;
  onLlmTest(): void;
  onRuntimeSettingsSave(values: RuntimeSettings): void;
  onLogout(): void;
}

export interface SessionView {
  bind(handlers: SessionViewHandlers): () => void;
  render(state: SessionState): void;
  renderIntro(data: SessionIntroResponse): void;
  renderExternalModels(settings: ExternalModelSettings): void;
  renderRuntimeSettings(settings: RuntimeSettings): void;
  setRuntimeSettingsBusy(busy: boolean): void;
  setRecoveryStatus(message: string, type?: 'error'): void;
  setRecoveryBusy(busy: boolean): void;
  setExternalModelsBusy(busy: boolean): void;
  setLlmBusy(busy: boolean): void;
  setLogoutBusy(busy: boolean): void;
}

function required<T extends Element>(document: Document, selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Session element not found: ${selector}`);
  return element;
}

export function createSessionView(document: Document): SessionView {
  const root = document.documentElement;
  const logout = required<HTMLButtonElement>(document, '#btn-logout');
  const llmTest = required<HTMLButtonElement>(document, '#btn-llm-test');
  const endedOverlay = required<HTMLElement>(document, '#ended-overlay');
  const recoveryInput = required<HTMLInputElement>(document, '#session-garmin-hours');
  const recoveryApply = required<HTMLButtonElement>(document, '#session-recovery-apply');
  const recoveryStatus = required<HTMLElement>(document, '#session-recovery-status');
  const toggle = required<HTMLInputElement>(document, '#external-models-toggle');
  const privacyStatus = required<HTMLElement>(document, '#external-models-status');
  const disclosure = required<HTMLElement>(document, '#external-model-disclosure');
  const localFeatures = required<HTMLElement>(document, '#external-model-local-features');
  const runtimeForm = required<HTMLFormElement>(document, '#runtime-settings-form');
  const runtimeSave = required<HTMLButtonElement>(document, '#runtime-settings-save');

  return {
    bind(handlers) {
      const apply = () => handlers.onRecoveryApply(recoveryInput.value);
      const keydown = (event: KeyboardEvent) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        apply();
      };
      const change = () => handlers.onExternalModelsChange(toggle.checked);
      const saveRuntime = (event: Event) => {
        event.preventDefault();
        const data = new FormData(runtimeForm);
        const optionalTokens = String(data.get('llm_max_tokens') ?? '').trim();
        handlers.onRuntimeSettingsSave({
          agent_max_steps: Number(data.get('agent_max_steps')),
          llm_temperature: Number(data.get('llm_temperature')),
          llm_max_tokens: optionalTokens ? Number(optionalTokens) : null,
          llm_timeout_seconds: Number(data.get('llm_timeout_seconds')),
          llm_max_retries: Number(data.get('llm_max_retries')),
          chat_timeout_seconds: Number(data.get('chat_timeout_seconds')),
        });
      };
      recoveryApply.addEventListener('click', apply);
      recoveryInput.addEventListener('keydown', keydown);
      toggle.addEventListener('change', change);
      runtimeForm.addEventListener('submit', saveRuntime);
      llmTest.addEventListener('click', handlers.onLlmTest);
      logout.addEventListener('click', handlers.onLogout);
      return () => {
        recoveryApply.removeEventListener('click', apply);
        recoveryInput.removeEventListener('keydown', keydown);
        toggle.removeEventListener('change', change);
        runtimeForm.removeEventListener('submit', saveRuntime);
        llmTest.removeEventListener('click', handlers.onLlmTest);
        logout.removeEventListener('click', handlers.onLogout);
      };
    },
    render(state) {
      root.dataset.session = state.status;
      logout.disabled = state.ended || state.status === 'ending';
      endedOverlay.classList.toggle('show', state.ended);
    },
    renderIntro(data) {
      recoveryInput.value = String(data.garmin_recovery_hours ?? 0);
    },
    renderExternalModels(settings) {
      const enabled = Boolean(settings.external_models_enabled);
      toggle.checked = enabled;
      toggle.disabled = false;
      privacyStatus.textContent = enabled
        ? '已启用：下方列出的数据可能按功能发送到对应的外部服务。'
        : '已关闭：不会向外部模型发送对话、档案、记忆、训练计划或餐盘照片。';
      privacyStatus.className = `privacy-status${enabled ? '' : ' disabled'}`;
      disclosure.replaceChildren();
      for (const item of settings.disclosure ?? []) {
        const row = document.createElement('div');
        row.className = 'privacy-disclosure-item';
        const title = document.createElement('strong');
        title.textContent = item.name || '外部服务';
        const text = document.createElement('span');
        text.textContent = `：${item.data || ''}`;
        row.append(title, text);
        disclosure.append(row);
      }
      const features = settings.local_features ?? [];
      localFeatures.textContent = features.length ? `关闭后仍可使用：${features.join('、')}。` : '';
    },
    renderRuntimeSettings(settings) {
      for (const [name, value] of Object.entries(settings)) {
        const field = runtimeForm.elements.namedItem(name);
        if (field instanceof HTMLInputElement) field.value = value == null ? '' : String(value);
      }
    },
    setRuntimeSettingsBusy(busy) {
      runtimeSave.disabled = busy;
      runtimeSave.textContent = busy ? '保存中…' : '保存参数';
    },
    setRecoveryStatus(text, type) {
      recoveryStatus.textContent = text;
      recoveryStatus.className = `session-recovery-status${type ? ` ${type}` : ''}`;
    },
    setRecoveryBusy(busy) {
      recoveryApply.disabled = busy;
    },
    setExternalModelsBusy(busy) {
      toggle.disabled = busy;
    },
    setLlmBusy(busy) {
      llmTest.disabled = busy;
      llmTest.textContent = busy ? '测试中…' : 'LLM 测试';
    },
    setLogoutBusy(busy) {
      logout.classList.toggle('loading', busy);
      logout.disabled = busy;
    },
  };
}
