import { chatApi } from '../../api/chat';
import { plansApi } from '../../api/plans';
import { settingsApi } from '../../api/settings';
import { localDateISO } from '../../shared/health-metrics';
import { isAbort } from '../../shared/operations';
import { isRecord } from '../../shared/validation';
import type { DomainContext, DomainController } from '../domain-factory';
import { createChatState } from './state';
import { createChatView } from './view';
export interface ChatDependencies {
  chat: typeof chatApi;
  plans: typeof plansApi;
  settings: typeof settingsApi;
}
const defaults: ChatDependencies = { chat: chatApi, plans: plansApi, settings: settingsApi };
const message = (error: unknown) => (error instanceof Error ? error.message : '请求失败');
function safeMarkdownFilename(value: unknown): string {
  const printable = Array.from(String(value || 'training-plan'), (character) =>
    character.charCodeAt(0) < 32 ? '-' : character,
  ).join('');
  const stem = printable
    .replace(/[<>:"/\\|?*]/g, '-')
    .replace(/[. ]+$/g, '')
    .trim();
  return `${stem || 'training-plan'}.md`;
}
function viewerMode(text: string): 'nutrition' | 'training' | null {
  const value = text.replaceAll(' ', '');
  if (
    ['饮食', '营养', '摄入', '餐食', '碳水', '蛋白质', '脂肪', '热量'].some((word) =>
      value.includes(word),
    )
  )
    return 'nutrition';
  if (
    ['训练', '运动', '锻炼', '动作组', '训练组', '卧推', '深蹲', '硬拉', '跑步', '跳绳'].some(
      (word) => value.includes(word),
    )
  )
    return 'training';
  return null;
}
export function createChatController(
  context: DomainContext,
  dependencies: ChatDependencies = defaults,
): DomainController {
  const state = createChatState();
  const view = createChatView(context.document, context.shell);
  const disposers: Array<() => void> = [];
  let started = false;
  async function send(text: string): Promise<void> {
    const session = context.state.sessionContext;
    if (state.pending || session.ended) return;
    const requestedMode = viewerMode(text);
    if (requestedMode) context.events.emit('data:view-mode', { mode: requestedMode });
    const history = session.conversationHistory
      .slice(-20)
      .map((item) => ({ role: item.role, content: item.text }));
    view.message('user', text);
    view.input('');
    state.pending = true;
    view.render(state, session.externalModelsEnabled, session.ended);
    const removeTyping = view.typing();
    const operation = context.operations.begin('chat-send');
    const source = state.source;
    const planContext = state.planContext;
    state.source = 'chat';
    state.planContext = null;
    try {
      const data = await dependencies.chat.send(
        {
          message: text,
          history,
          source,
          plan_context: planContext,
          garmin_recovery_hours: session.garminRecoveryHours,
          soreness_prompt_regions: [...session.sorenessPromptRegions],
          pending_memory_entry_ids: [...state.pendingMemoryEntryIds],
        },
        { signal: operation.signal },
      );
      if (!operation.isCurrent()) return;
      session.sorenessPromptRegions.splice(0);
      const reply = String(data.reply ?? data.error ?? '请求失败');
      view.message(
        'bot',
        reply,
        isRecord(data.artifact) ? data.artifact : null,
        Boolean(data.error),
      );
      if (isRecord(data.profile_update))
        view.message('bot', '检测到档案更新，请确认是否保存。', {
          ...data.profile_update,
          type: 'profile_update',
        });
      view.validation(data.plan_validation);
      view.workflow(data.workflow_state);
      if (isRecord(data.memory_candidate))
        context.shell.toast.success(
          `已生成待确认记忆：${String(data.memory_candidate.summary ?? '请在数据管理中查看并确认。')}`,
        );
      if (data.memory_confirmation) state.pendingMemoryEntryIds = [];
      if (Array.isArray(data.memory_candidates))
        state.pendingMemoryEntryIds = data.memory_candidates
          .filter(isRecord)
          .map((item) => String(item.entry_id ?? ''))
          .filter(Boolean);
      else if (isRecord(data.memory_candidate) && data.memory_candidate.entry_id)
        state.pendingMemoryEntryIds = [String(data.memory_candidate.entry_id)];
      if (data.reply) {
        session.conversationHistory.push(
          { role: 'user', text },
          { role: 'assistant', text: reply },
        );
        if (session.conversationHistory.length > 20)
          session.conversationHistory.splice(0, session.conversationHistory.length - 20);
      }
      context.events.emit('workout:refresh');
      if (isRecord(data.artifact)) {
        const type = data.artifact.type;
        const mode =
          type === 'nutrition_records'
            ? 'nutrition'
            : ['training_records', 'training_plan'].includes(String(type))
              ? 'training'
              : requestedMode;
        if (mode)
          context.events.emit('data:view-mode', {
            mode,
            day: String(
              data.artifact.default_date ?? data.artifact.suggested_date ?? localDateISO(),
            ),
          });
      }
    } catch (error) {
      if (!isAbort(error) && operation.isCurrent())
        view.message('bot', `请求失败：${message(error)}`, null, true);
    } finally {
      removeTyping();
      state.pending = false;
      view.render(state, session.externalModelsEnabled, session.ended);
    }
  }
  async function savePlan(
    artifact: Record<string, unknown>,
    values: Record<string, unknown>,
  ): Promise<void> {
    const operation = context.operations.begin('chat-save-plan');
    try {
      const result = await dependencies.plans.create(
        {
          ...values,
          content: String(artifact.content ?? ''),
          source: artifact.source,
          draft_id: String(artifact.draft_id ?? ''),
        },
        operation.signal,
      );
      if (operation.isCurrent())
        context.shell.toast.success(`训练计划已保存：${String(result.filename ?? '')}`);
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(message(error));
    }
  }
  function downloadPlan(artifact: Record<string, unknown>, values: Record<string, unknown>): void {
    const blob = new Blob([String(artifact.content ?? '')], {
      type: 'text/markdown;charset=utf-8',
    });
    const url = URL.createObjectURL(blob);
    const anchor = context.document.createElement('a');
    anchor.href = url;
    anchor.download = safeMarkdownFilename(values.title ?? artifact.title);
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function confirmProfile(candidate: Record<string, unknown>): Promise<void> {
    const operation = context.operations.begin('chat-profile');
    try {
      await dependencies.settings.confirmProfileUpdate(candidate, operation.signal);
      if (operation.isCurrent()) context.shell.toast.success('档案更新已确认。');
    } catch (error) {
      if (!isAbort(error)) context.shell.toast.error(message(error));
    }
  }
  return {
    start() {
      if (started) return;
      started = true;
      disposers.push(
        view.bind({
          onSubmit: (text) => void send(text),
          onSavePlan: (artifact, values) => void savePlan(artifact, values),
          onDownloadPlan: downloadPlan,
          onConfirmProfile: (candidate) => void confirmProfile(candidate),
        }),
        context.events.on('chat:message', ({ role, text }) => view.message(role, text)),
        context.events.on('chat:submit-plan', ({ content, subject }) => {
          state.source = 'uploaded_plan';
          state.planContext = { suggested_date: localDateISO(), subject };
          view.input(`我上传了一份训练计划，科目是“${subject}”。请解析并优化：\n\n${content}`);
          void send(view.input());
        }),
        context.events.on('session:changed', () => {
          const session = context.state.sessionContext;
          view.render(state, session.externalModelsEnabled, session.ended);
        }),
      );
      const session = context.state.sessionContext;
      view.render(state, session.externalModelsEnabled, session.ended);
    },
    stop() {
      if (!started) return;
      started = false;
      disposers.splice(0).forEach((dispose) => dispose());
      for (const key of ['chat-send', 'chat-save-plan', 'chat-profile'])
        context.operations.cancel(key);
    },
  };
}
