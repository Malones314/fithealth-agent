import type { AppShell } from '../../app/shell';
import type { JsonObject } from '../../shared/types';
import type { ChatDomainState } from './types';

export interface ChatViewHandlers {
  onSubmit(text: string): void;
  onSavePlan(artifact: JsonObject, values: JsonObject): void;
  onDownloadPlan(artifact: JsonObject, values: JsonObject): void;
  onConfirmProfile(candidate: JsonObject): void;
}
export interface ChatView {
  bind(handlers: ChatViewHandlers): () => void;
  input(text?: string): string;
  message(role: 'user' | 'bot', text: string, artifact?: JsonObject | null, error?: boolean): void;
  typing(): () => void;
  render(state: ChatDomainState, enabled: boolean, ended: boolean): void;
  validation(value: unknown): void;
  workflow(value: unknown): void;
}
function required<T extends Element>(document: Document, selector: string): T {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Chat element not found: ${selector}`);
  return node;
}
function field(
  document: Document,
  label: string,
  name: string,
  value: unknown,
  multiline = false,
): HTMLElement {
  const wrap = document.createElement('div');
  wrap.className = 'plan-save-field';
  const title = document.createElement('label');
  title.textContent = label;
  const input = document.createElement(multiline ? 'textarea' : 'input');
  input.setAttribute('name', name);
  input.value = String(value ?? '');
  wrap.append(title, input);
  return wrap;
}

export function createChatView(document: Document, shell: AppShell): ChatView {
  const host = required<HTMLElement>(document, '#chat');
  const form = required<HTMLFormElement>(document, '#form');
  const input = required<HTMLTextAreaElement>(document, '#message');
  const send = required<HTMLButtonElement>(document, '#send');
  const hint = document.querySelector('#hint');
  let handlers: ChatViewHandlers | null = null;
  const listeners: Array<[EventTarget, string, EventListener]> = [];
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };
  function scroll(): void {
    host.scrollTop = host.scrollHeight;
  }
  function appendPlan(message: HTMLElement, artifact: JsonObject): void {
    const panel = document.createElement('form');
    panel.className = 'plan-save-panel';
    const grid = document.createElement('div');
    grid.className = 'plan-save-grid';
    const memo = field(document, '备忘', 'memo', '', true);
    memo.classList.add('full');
    grid.append(
      field(document, '日期', 'date', artifact.suggested_date),
      field(document, '科目', 'subject', artifact.subject),
      field(document, '标题', 'title', artifact.title),
      memo,
    );
    panel.append(grid);
    const actions = document.createElement('div');
    actions.className = 'plan-save-actions';
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'data-action';
    save.dataset.planSave = '1';
    save.textContent = '保存训练计划';
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'data-action';
    download.dataset.planDownload = '1';
    download.textContent = '下载';
    download.addEventListener('click', () => {
      handlers?.onDownloadPlan(artifact, Object.fromEntries(new FormData(panel)));
    });
    actions.append(save, download);
    panel.append(actions);
    panel.addEventListener('submit', (event) => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(panel));
      handlers?.onSavePlan(artifact, values);
    });
    message.append(panel);
  }
  function appendProfile(message: HTMLElement, candidate: JsonObject): void {
    const panel = document.createElement('div');
    panel.className = 'profile-update-panel';
    const summary = document.createElement('p');
    summary.textContent = String(candidate.summary ?? '检测到档案更新');
    const equipmentDiff = candidate.equipment_diff;
    const diff =
      equipmentDiff && typeof equipmentDiff === 'object' && !Array.isArray(equipmentDiff)
        ? (equipmentDiff as JsonObject)
        : {};
    const changes = document.createElement('div');
    changes.className = 'profile-equipment-diff';
    for (const [label, values] of [
      ['新增器械', diff.added],
      ['移除器械', diff.removed],
    ] as const) {
      if (!Array.isArray(values) || !values.length) continue;
      const line = document.createElement('p');
      line.textContent = `${label}：${values.map(String).join('、')}`;
      changes.append(line);
    }
    const confirm = document.createElement('button');
    confirm.type = 'button';
    confirm.className = 'data-action';
    confirm.textContent = '确认保存档案更新';
    confirm.addEventListener('click', () => handlers?.onConfirmProfile(candidate));
    panel.append(summary, changes, confirm);
    message.append(panel);
  }
  return {
    bind(next) {
      handlers = next;
      listen(form, 'submit', (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (text) next.onSubmit(text);
      });
      listen(input, 'keydown', (event) => {
        const key = event as KeyboardEvent;
        if (key.key === 'Enter' && !key.shiftKey && !key.isComposing) {
          key.preventDefault();
          form.requestSubmit();
        }
      });
      return () =>
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
    },
    input(text) {
      if (text !== undefined) input.value = text;
      return input.value;
    },
    message(role, text, artifact, error = false) {
      hint?.remove();
      const node = shell.message(role, text, { className: error ? 'notify-error' : undefined });
      if (artifact?.type === 'training_plan') appendPlan(node, artifact);
      if (artifact?.type === 'profile_update') appendProfile(node, artifact);
      scroll();
    },
    typing() {
      hint?.remove();
      const node = document.createElement('div');
      node.className = 'msg bot typing';
      node.append(
        document.createElement('span'),
        document.createElement('span'),
        document.createElement('span'),
      );
      host.append(node);
      scroll();
      return () => node.remove();
    },
    render(state, enabled, ended) {
      document.documentElement.dataset.chatPending = String(state.pending);
      send.disabled = state.pending || ended || !enabled;
      input.disabled = ended;
      input.placeholder = enabled
        ? '例如：把第2和第3组合并；或：今天卧推 60kg×5组'
        : '联网模型已关闭；本地健康查询、导入、训练编辑和数据管理仍可使用';
    },
    validation(value) {
      if (!value || typeof value !== 'object') return;
      const data = value as JsonObject;
      const violations = Array.isArray(data.violations) ? data.violations : [];
      if (data.passed === false || violations.length)
        shell.toast.error(
          `这份计划没有通过确定性校验：\n${violations.length ? violations.map(String).join('\n') : '（后端未给出明细）'}`,
        );
    },
    workflow(value) {
      const blocked = ['needs_clarification', 'constraint_conflict', 'validation_failed'].includes(
        String(value ?? ''),
      );
      document.querySelectorAll<HTMLButtonElement>('[data-plan-save]').forEach((button) => {
        if (button.dataset.planSaveDone !== '1') {
          button.disabled = blocked;
          button.title = blocked ? '当前存在未解决的冲突或待澄清项，暂不能保存计划' : '';
        }
      });
    },
  };
}
