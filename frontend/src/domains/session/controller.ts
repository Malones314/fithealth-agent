import { healthApi } from '../../api/health';
import { sessionApi } from '../../api/session';
import { settingsApi } from '../../api/settings';
import { configureChatTimeout } from '../../api/chat';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { DomainContext, DomainController } from '../domain-factory';
import { sessionState, subscribeSessionState, updateSessionState } from './state';
import type { ExternalModelSettings, RuntimeSettings } from './types';
import { createSessionView } from './view';

const PROFILE_FIELD_LABELS: Record<string, string> = {
  weekly_weight_kg: '体重',
  height_cm: '身高',
  birth_date: '出生日期',
  sex: '性别',
  goal: '训练目标',
};

export interface SessionControllerDependencies {
  session: typeof sessionApi;
  settings: typeof settingsApi;
  health: Pick<typeof healthApi, 'storageStatus'>;
}

const defaultDependencies: SessionControllerDependencies = {
  session: sessionApi,
  settings: settingsApi,
  health: healthApi,
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败';
}

function statusOf(error: unknown): number | undefined {
  if (!(error instanceof Error) || !('status' in error)) return undefined;
  const status = (error as Error & { status?: unknown }).status;
  return typeof status === 'number' ? status : undefined;
}

export function validateSessionGarminHours(raw: string): number {
  const text = raw.trim();
  if (!text) return 0;
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0 || value > 96) {
    throw new Error('请输入0–96.0 之间的数字。');
  }
  return Math.round(value * 10) / 10;
}

export function createSessionController(
  context: DomainContext,
  dependencies: SessionControllerDependencies = defaultDependencies,
): DomainController {
  const view = createSessionView(context.document);
  const state = sessionState;
  const disposers: Array<() => void> = [];
  let started = false;

  const render = () => view.render(state);

  async function initialize(): Promise<void> {
    const operation = context.operations.begin('session-startup');
    updateSessionState(state, { status: 'loading', message: undefined });
    const [intro, settings, runtime, profile, storage] = await Promise.allSettled([
      dependencies.session.intro(0, operation.signal),
      dependencies.settings.externalModels(operation.signal),
      dependencies.settings.runtime(operation.signal),
      dependencies.settings.profileStatus(operation.signal),
      dependencies.health.storageStatus(operation.signal),
    ]);
    if (!operation.isCurrent()) return;

    if (intro.status === 'fulfilled') {
      const garminRecoveryHours = Number(intro.value.garmin_recovery_hours ?? 0);
      updateSessionState(state, {
        garminRecoveryHours,
        sorenessPromptRegions: Array.isArray(intro.value.soreness_prompt_regions)
          ? [...intro.value.soreness_prompt_regions]
          : [],
      });
      view.renderIntro(intro.value);
      if (intro.value.message) {
        context.shell.message('bot', intro.value.message, {
          className: 'session-intro',
          replaceClass: 'session-intro',
        });
      }
      view.setRecoveryStatus(
        `已应用到本次会话：${garminRecoveryHours} 小时；刷新页面后需要重新输入。`,
      );
    } else if (!isAbort(intro.reason)) {
      context.shell.toast.error(`无法读取会话介绍：${messageOf(intro.reason)}`);
    }

    if (settings.status === 'fulfilled') {
      const value = settings.value as ExternalModelSettings;
      updateSessionState(state, {
        externalModelsEnabled: Boolean(value.external_models_enabled),
      });
      view.renderExternalModels(value);
    } else if (!isAbort(settings.reason)) {
      context.shell.toast.error(`无法读取外部模型设置：${messageOf(settings.reason)}`);
    }

    if (runtime.status === 'fulfilled') {
      const values = runtime.value as unknown as RuntimeSettings;
      view.renderRuntimeSettings(values);
      configureChatTimeout(values.chat_timeout_seconds);
    } else if (!isAbort(runtime.reason)) {
      context.shell.toast.error(`无法读取运行参数：${messageOf(runtime.reason)}`);
    }

    if (profile.status === 'fulfilled' && isRecord(profile.value) && !profile.value.complete) {
      const missing = Array.isArray(profile.value.missing_fields)
        ? profile.value.missing_fields.filter((field): field is string => typeof field === 'string')
        : [];
      const missingText = missing.length
        ? `（还缺：${missing.map((field) => PROFILE_FIELD_LABELS[field] || field).join('、')}）`
        : '';
      const prompt =
        typeof profile.value.prompt === 'string' ? profile.value.prompt : '请先补全个人档案';
      context.shell.toast.info(`${prompt}${missingText}`);
    } else if (profile.status === 'rejected' && !isAbort(profile.reason)) {
      context.shell.toast.error(`无法读取档案状态：${messageOf(profile.reason)}`);
    }

    if (storage.status === 'fulfilled' && isRecord(storage.value)) {
      const text = typeof storage.value.message === 'string' ? storage.value.message : '';
      if (text) {
        if (storage.value.available) context.shell.toast.info(text);
        else context.shell.toast.error(text);
      }
    } else if (storage.status === 'rejected' && !isAbort(storage.reason)) {
      context.shell.toast.error(`无法读取存储状态：${messageOf(storage.reason)}`);
    }
    updateSessionState(state, { status: 'ready' });
    context.state.session = 'ready';
    context.events.emit('session:changed');
  }

  async function refreshIntro(raw: string): Promise<void> {
    let value: number;
    try {
      value = validateSessionGarminHours(raw);
    } catch (error) {
      view.setRecoveryStatus(messageOf(error), 'error');
      return;
    }
    const operation = context.operations.begin('session-intro');
    view.setRecoveryBusy(true);
    view.setRecoveryStatus('正在刷新肌群恢复清单…');
    try {
      const intro = await dependencies.session.intro(value, operation.signal);
      if (!operation.isCurrent()) return;
      updateSessionState(state, {
        garminRecoveryHours: Number(intro.garmin_recovery_hours ?? value),
        sorenessPromptRegions: Array.isArray(intro.soreness_prompt_regions)
          ? [...intro.soreness_prompt_regions]
          : [],
      });
      view.renderIntro(intro);
      if (intro.message) {
        context.shell.message('bot', intro.message, {
          className: 'session-intro',
          replaceClass: 'session-intro',
        });
      }
      view.setRecoveryStatus(
        `已应用到本次会话：${state.garminRecoveryHours} 小时；刷新页面后需要重新输入。`,
      );
    } catch (error) {
      if (!isAbort(error)) view.setRecoveryStatus(messageOf(error), 'error');
    } finally {
      if (operation.isCurrent()) view.setRecoveryBusy(false);
    }
  }

  async function updateExternalModels(enabled: boolean): Promise<void> {
    const previous = state.externalModelsEnabled;
    const operation = context.operations.begin('external-model-settings');
    view.setExternalModelsBusy(true);
    try {
      const result = (await dependencies.settings.updateExternalModels(
        enabled,
        operation.signal,
      )) as ExternalModelSettings;
      if (!operation.isCurrent()) return;
      updateSessionState(state, {
        externalModelsEnabled: Boolean(result.external_models_enabled),
      });
      context.events.emit('session:changed');
      view.renderExternalModels(result);
      context.shell.toast.success(
        state.externalModelsEnabled ? '已允许外部模型处理数据' : '已关闭外部模型数据外发',
      );
    } catch (error) {
      if (isAbort(error)) return;
      updateSessionState(state, { externalModelsEnabled: previous });
      context.events.emit('session:changed');
      view.renderExternalModels({ external_models_enabled: previous });
      context.shell.toast.error(messageOf(error));
    } finally {
      if (operation.isCurrent()) view.setExternalModelsBusy(false);
    }
  }

  async function testLlm(): Promise<void> {
    const operation = context.operations.begin('llm-connectivity');
    view.setLlmBusy(true);
    try {
      const result = await dependencies.settings.testLlm(operation.signal);
      if (!operation.isCurrent()) return;
      if (result.ok) {
        context.shell.toast.success(
          `LLM 连通性正常（${String(result.model || '当前模型')}，${Number(result.latency_ms || 0)} ms）`,
        );
      } else {
        context.shell.toast.error(
          typeof result.message === 'string' ? result.message : 'LLM 连通性测试失败。',
        );
      }
    } catch (error) {
      if (!isAbort(error)) {
        const text = `LLM 连通性测试失败：${messageOf(error)}`;
        if (statusOf(error) === 403) context.shell.toast.info(text);
        else context.shell.toast.error(text);
      }
    } finally {
      if (operation.isCurrent()) view.setLlmBusy(false);
    }
  }

  async function updateRuntimeSettings(values: RuntimeSettings): Promise<void> {
    const operation = context.operations.begin('runtime-settings');
    view.setRuntimeSettingsBusy(true);
    try {
      const result = (await dependencies.settings.updateRuntime(
        { ...values },
        operation.signal,
      )) as unknown as RuntimeSettings;
      if (!operation.isCurrent()) return;
      view.renderRuntimeSettings(result);
      configureChatTimeout(result.chat_timeout_seconds);
      context.shell.toast.success('运行参数已保存，将应用于后续请求。');
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(`运行参数保存失败：${messageOf(error)}`);
    } finally {
      if (operation.isCurrent()) view.setRuntimeSettingsBusy(false);
    }
  }

  async function logout(): Promise<void> {
    if (state.ended || state.status === 'ending') return;
    const operation = context.operations.begin('session-logout');
    updateSessionState(state, { status: 'ending' });
    view.setLogoutBusy(true);
    try {
      const result = await dependencies.session.logout(
        [...state.conversationHistory],
        operation.signal,
      );
      if (!operation.isCurrent()) return;
      if (result.saved) {
        const summary =
          typeof result.summary === 'string' ? result.summary.slice(0, 200) : undefined;
        const confirmed = Boolean(result.user_confirmed);
        context.shell.toast.show(
          confirmed
            ? '已退出并保存对话摘要（3天后自动删除）'
            : '已退出并生成待确认记忆，请在数据管理中确认后用于后续对话',
          { level: confirmed ? 'success' : 'info', summary },
        );
      } else if (result.status === 'not_saved') {
        const stages: Record<string, string> = {
          level1: '含敏感信息',
          level3: 'AI判断',
          external_models_disabled: '外部模型已关闭',
        };
        const label = stages[String(result.pipeline_stage || '')] || '';
        const validation = result.fact_validation ? `；${String(result.fact_validation)}` : '';
        context.shell.toast.info(
          `已退出，未保存摘要（${label}：${String(result.reason || '')}${validation}）`,
        );
      } else if (result.status === 'no_messages') {
        context.shell.toast.info('无对话内容，直接退出');
      } else {
        context.shell.toast.error(`退出时发生错误：${String(result.reason || '')}`);
      }
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(`网络异常：${messageOf(error)}`);
    } finally {
      updateSessionState(state, { status: 'ended', ended: true });
      context.state.session = 'ended';
      context.events.emit('session:changed');
      view.setLogoutBusy(false);
      context.operations.cancelAll();
    }
  }

  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        subscribeSessionState(state, render),
        view.bind({
          onRecoveryApply: (value) => void refreshIntro(value),
          onExternalModelsChange: (enabled) => void updateExternalModels(enabled),
          onLlmTest: () => void testLlm(),
          onRuntimeSettingsSave: (values) => void updateRuntimeSettings(values),
          onLogout: () => void logout(),
        }),
        context.events.on('startup', () => void initialize()),
      );
      render();
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      for (const key of [
        'session-startup',
        'session-intro',
        'external-model-settings',
        'llm-connectivity',
        'runtime-settings',
        'session-logout',
      ]) {
        context.operations.cancel(key);
      }
    },
  };
}
