import type { AppShell } from '../../app/shell';
import { setSanitizedMarkdown } from '../../shared/sanitize';
import type { JsonObject } from '../../shared/types';
import type { DataManagementState } from './types';
export type DataAction =
  | 'delete-records'
  | 'delete-plans'
  | 'clear-memories'
  | 'delete-pending'
  | 'reset-profile'
  | 'reset-all'
  | 'export-backup'
  | 'import-backup'
  | 'add-soreness'
  | 'view-record';
export interface DataViewHandlers {
  onOpen(): void;
  onClose(): void;
  onAction(action: DataAction): void;
  onPlan(action: 'view' | 'edit' | 'delete' | 'download', plan: JsonObject): void;
  onMemory(action: string, memory: JsonObject, fact?: JsonObject, value?: string): void;
  onSoreness(action: 'edit' | 'delete', item: JsonObject): void;
  onHealthDelete(item: JsonObject): void;
  onAuditDelete(item: JsonObject): void;
  onQuarantine(action: 'preview' | 'restore' | 'dismiss' | 'delete', item: JsonObject): void;
  onRecovery(action: 'download' | 'delete', item: JsonObject): void;
  onViewer(mode: 'training' | 'nutrition', day: string, explicit?: boolean): void;
  onNutrition(action: 'save' | 'delete', item: JsonObject, patch?: JsonObject): void;
  onBackup(file: File): void;
}
export interface DataManagementView {
  bind(handlers: DataViewHandlers): () => void;
  render(state: DataManagementState): void;
  renderViewer(state: DataManagementState): void;
  renderAudit(items: JsonObject[]): void;
  renderQuarantine(items: JsonObject[]): void;
  renderRecovery(items: JsonObject[]): void;
  renderResetResult(result: JsonObject, onRetry: (keys: string[]) => void): void;
  status(text?: string, tone?: string): void;
  previewPlan(plan: JsonObject): void;
  previewRecord(data: JsonObject): void;
  backupInput(): HTMLInputElement;
  resetConfirmation(): string;
  selectedRecords(): string[];
  selectedPlans(): string[];
}
function required<T extends Element>(document: Document, selector: string): T {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Data element not found: ${selector}`);
  return node;
}
const array = (value: unknown): JsonObject[] =>
  Array.isArray(value)
    ? value.filter(
        (item): item is JsonObject =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item),
      )
    : [];
const time = (value: unknown) => {
  if (!value) return '';
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN');
};
function button(
  document: Document,
  text: string,
  action: () => void,
  danger = false,
): HTMLButtonElement {
  const node = document.createElement('button');
  node.type = 'button';
  node.className = `small-delete${danger ? ' danger' : ''}`;
  node.textContent = text;
  node.addEventListener('click', action);
  return node;
}
function nutritionField(
  document: Document,
  label: string,
  name: string,
  value: unknown,
  type: 'text' | 'number' | 'textarea' = 'number',
): HTMLElement {
  const field = document.createElement('label');
  field.className = 'nutrition-field';
  const caption = document.createElement('span');
  caption.textContent = label;
  const input =
    type === 'textarea' ? document.createElement('textarea') : document.createElement('input');
  input.setAttribute('name', name);
  input.value = String(value ?? '');
  if (type === 'number') {
    const numeric = input as HTMLInputElement;
    numeric.type = 'number';
    numeric.min = '0';
    numeric.step = name === 'calories_kcal' ? '1' : '0.1';
  }
  field.append(caption, input);
  return field;
}
export function createDataManagementView(document: Document, shell: AppShell): DataManagementView {
  const status = required<HTMLElement>(document, '#data-status');
  const viewerDate = required<HTMLInputElement>(document, '#viewer-date');
  const viewerSelect = required<HTMLSelectElement>(document, '#viewer-record-select');
  const viewerDetail = required<HTMLElement>(document, '#viewer-detail');
  const listeners: Array<[EventTarget, string, EventListener]> = [];
  let handlers: DataViewHandlers | null = null;
  let current: DataManagementState | null = null;
  let previewedPlan: JsonObject | null = null;
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };
  const renderRows = (
    id: string,
    countId: string,
    items: JsonObject[],
    empty: string,
    render: (item: JsonObject) => Node,
  ) => {
    const host = required<HTMLElement>(document, `#${id}`);
    required<HTMLElement>(document, `#${countId}`).textContent = `${items.length} 条`;
    host.replaceChildren();
    if (!items.length) shell.empty(host, empty, 'data');
    else shell.list(host, items, render);
  };
  const selected = (host: string) =>
    Array.from(document.querySelectorAll<HTMLInputElement>(`#${host} input:checked`)).map(
      (node) => node.value,
    );
  function renderRecords(items: JsonObject[]) {
    renderRows('records-list', 'records-count', items, '暂无已保存训练记录', (item) => {
      const row = document.createElement('label');
      row.className = 'data-row';
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.value = String(item.id);
      check.checked = current?.selectedRecordIds.has(check.value) ?? false;
      check.addEventListener('change', () => {
        if (check.checked) current?.selectedRecordIds.add(check.value);
        else current?.selectedRecordIds.delete(check.value);
        updateButtons();
      });
      const main = document.createElement('div');
      main.className = 'data-row-main';
      const title = document.createElement('div');
      title.className = 'data-row-title';
      title.textContent = `${String(item.date ?? '未知日期')} · ${String(item.sport ?? '未知运动')}`;
      const meta = document.createElement('div');
      meta.className = 'data-row-meta';
      meta.textContent = `${Number(item.total_sets ?? 0)} 组${item.source_file ? ` · ${item.source_file}` : ''}`;
      main.append(title, meta);
      row.append(check, main);
      return row;
    });
  }
  function renderPlans(items: JsonObject[]) {
    renderRows('plans-list', 'plans-count', items, '暂无已保存训练计划', (item) => {
      const row = document.createElement('div');
      row.className = 'data-row';
      const check = document.createElement('input');
      check.type = 'checkbox';
      check.value = String(item.id);
      check.addEventListener('change', updateButtons);
      const main = document.createElement('div');
      main.className = 'data-row-main';
      const title = document.createElement('div');
      title.className = 'data-row-title';
      title.textContent = String(item.title ?? '未命名计划');
      const meta = document.createElement('div');
      meta.className = 'data-row-meta';
      meta.textContent = `${String(item.date ?? '')} · ${String(item.subject ?? '')} · ${String(item.filename ?? '')}${item.memo ? ' · 有备忘' : ''}`;
      const actions = document.createElement('div');
      actions.className = 'plan-row-actions';
      actions.append(
        button(document, '查看', () => handlers?.onPlan('view', item)),
        button(document, '编辑', () => handlers?.onPlan('edit', item)),
        button(document, '删除', () => handlers?.onPlan('delete', item), true),
      );
      main.append(title, meta, actions);
      row.append(check, main);
      return row;
    });
  }
  function renderMemories(items: JsonObject[]) {
    renderRows('memories-list', 'memories-count', items, '暂无临时记忆', (memory) => {
      const row = document.createElement('div');
      row.className = 'data-row';
      const main = document.createElement('div');
      main.className = 'data-row-main';
      const title = document.createElement('div');
      title.className = 'data-row-title';
      title.textContent = String(memory.summary ?? '空摘要');
      const meta = document.createElement('div');
      meta.className = 'data-row-meta';
      meta.textContent = `重要度 ${Number(memory.importance ?? 1)}/5 · 创建：${time(memory.created_at)}`;
      main.append(title, meta);
      array(memory.facts)
        .filter((fact) => fact.status !== 'rejected')
        .forEach((fact, index) => {
          const factRow = document.createElement('div');
          factRow.className = 'memory-fact-row';
          const body = document.createElement('div');
          let notice = '';
          if (fact.key === 'weekly_schedule') {
            try {
              const schedule = JSON.parse(String(fact.value ?? '{}'));
              const skipped = Array.isArray(schedule.invalid_days) ? schedule.invalid_days : [];
              if (skipped.length)
                notice = ` · 已识别 ${Object.keys(schedule.days ?? {}).length} 天，跳过：${skipped.join('、')}`;
            } catch {
              // Malformed legacy schedules remain visible as raw fact values.
            }
          }
          body.textContent = `${String(fact.key ?? '事实')}：${String(fact.value ?? '')}${notice} · ${fact.user_confirmed ? '已确认' : '待确认'}`;
          const actions = document.createElement('div');
          actions.className = 'plan-row-actions';
          if (!fact.user_confirmed)
            actions.append(
              button(document, '确认', () =>
                handlers?.onMemory('confirm-fact', memory, {
                  ...fact,
                  factRef: String(fact.fact_id ?? index),
                }),
              ),
            );
          actions.append(
            button(document, '拒绝', () =>
              handlers?.onMemory('reject-fact', memory, {
                ...fact,
                factRef: String(fact.fact_id ?? index),
              }),
            ),
            button(document, '编辑', () =>
              handlers?.onMemory('edit-fact', memory, {
                ...fact,
                factRef: String(fact.fact_id ?? index),
              }),
            ),
          );
          if (Array.isArray(fact.history) && fact.history.length)
            actions.append(
              button(document, '回滚', () =>
                handlers?.onMemory('rollback-fact', memory, {
                  ...fact,
                  factRef: String(fact.fact_id ?? index),
                }),
              ),
            );
          factRow.append(body, actions);
          main.append(factRow);
        });
      const actions = document.createElement('div');
      actions.className = 'plan-row-actions';
      if (!memory.user_confirmed)
        actions.append(button(document, '确认记忆', () => handlers?.onMemory('confirm', memory)));
      actions.append(button(document, '删除', () => handlers?.onMemory('delete', memory), true));
      main.append(actions);
      row.append(main);
      return row;
    });
  }
  function simpleList(
    id: string,
    countId: string,
    items: JsonObject[],
    empty: string,
    title: (item: JsonObject) => string,
    actions: (item: JsonObject) => Node[],
  ) {
    renderRows(id, countId, items, empty, (item) => {
      const row = document.createElement('div');
      row.className = 'data-row';
      const main = document.createElement('div');
      main.className = 'data-row-main';
      const heading = document.createElement('div');
      heading.className = 'data-row-title';
      heading.textContent = title(item);
      const meta = document.createElement('div');
      meta.className = 'data-row-meta';
      meta.textContent = time(item.created_at);
      main.append(heading, meta);
      row.append(main, ...actions(item));
      return row;
    });
  }
  function updateButtons() {
    const records = selected('records-list');
    const plans = selected('plans-list');
    const delRecords = required<HTMLButtonElement>(document, '#delete-records');
    const view = required<HTMLButtonElement>(document, '#view-record');
    const delPlans = required<HTMLButtonElement>(document, '#delete-plans');
    delRecords.disabled = !records.length;
    view.disabled = records.length !== 1;
    delPlans.disabled = !plans.length;
  }
  return {
    bind(next) {
      handlers = next;
      listen(required(document, '#btn-data'), 'click', next.onOpen);
      listen(required(document, '#data-modal-close'), 'click', next.onClose);
      shell.modal('data').onRequestClose = next.onClose;
      for (const action of [
        'delete-records',
        'delete-plans',
        'clear-memories',
        'delete-pending',
        'reset-profile',
        'reset-all',
        'export-backup',
        'import-backup',
        'add-soreness',
        'view-record',
      ] as DataAction[])
        listen(required(document, `#${action}`), 'click', () => next.onAction(action));
      const backup = required<HTMLInputElement>(document, '#backup-file');
      listen(backup, 'change', () => {
        const file = backup.files?.[0];
        backup.value = '';
        if (file) next.onBackup(file);
      });
      listen(required(document, '#record-preview-close'), 'click', () =>
        required(document, '#record-preview').classList.remove('show'),
      );
      listen(required(document, '#plan-preview-close'), 'click', () =>
        required(document, '#plan-preview').classList.remove('show'),
      );
      listen(
        required(document, '#plan-preview-download'),
        'click',
        () => previewedPlan && next.onPlan('download', previewedPlan),
      );
      listen(viewerDate, 'change', () =>
        next.onViewer(current?.viewerMode ?? 'training', viewerDate.value),
      );
      listen(viewerSelect, 'change', () =>
        next.onViewer(current?.viewerMode ?? 'training', viewerDate.value, true),
      );
      const tabs = shell.registerTabs(
        'viewer-mode',
        required(document, '.data-viewer-switch'),
        [required(document, '#viewer-training'), required(document, '#viewer-nutrition')],
        {
          ariaLabel: '选择右侧数据类型',
          readValue: (node) => (node.id === 'viewer-nutrition' ? 'nutrition' : 'training'),
          initial: 'training',
          onChange: (mode) => next.onViewer(mode, viewerDate.value),
        },
      );
      return () => {
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
        tabs.destroy();
      };
    },
    render(state) {
      current = state;
      document.documentElement.dataset.dataManagementLoading = String(state.loading);
      if (!state.overview) return;
      const data = state.overview;
      renderRecords(array(data.records));
      renderPlans(array(data.plans));
      renderMemories(array(data.memories));
      simpleList(
        'daily-records-list',
        'daily-records-count',
        array(data.daily_records),
        '暂无每日记录',
        (item) => `${String(item.date ?? '')} · 每日记录`,
        (item) => [button(document, '删除', () => handlers?.onMemory('delete-daily', item), true)],
      );
      simpleList(
        'soreness-list',
        'soreness-count',
        array(data.soreness_reports),
        '暂无酸痛记录',
        (item) => `${String(item.region ?? '')} · ${String(item.level ?? '')}`,
        (item) => [
          button(document, '编辑', () => handlers?.onSoreness('edit', item)),
          button(document, '删除', () => handlers?.onSoreness('delete', item), true),
        ],
      );
      simpleList(
        'health-imports-list',
        'health-imports-count',
        array(data.health_imports),
        '暂无全天健康或睡眠导入',
        (item) => String(item.filename ?? '未命名健康数据'),
        (item) => [button(document, '删除', () => handlers?.onHealthDelete(item), true)],
      );
      required<HTMLElement>(document, '#profile-data-summary').textContent = data.profile_complete
        ? '档案已完整'
        : '档案尚未完整';
      const pending = required<HTMLButtonElement>(document, '#delete-pending');
      pending.disabled = !data.has_pending_workout;
      pending.textContent = data.has_pending_workout ? '删除待确认训练' : '无待确认训练';
      updateButtons();
    },
    renderViewer(state) {
      current = state;
      viewerDate.value = state.viewerDay;
      viewerSelect.replaceChildren(
        ...state.viewerItems.map((item) => {
          const option = document.createElement('option');
          option.value = String(item.id);
          option.textContent = String(item.name ?? item.title ?? item.id);
          return option;
        }),
      );
      viewerSelect.disabled = !state.viewerItems.length;
      required<HTMLElement>(document, '#viewer-content-title').textContent =
        state.viewerMode === 'nutrition' ? '营养组列表' : '训练组列表';
      viewerDetail.hidden = state.viewerMode === 'training';
      if (!state.viewerItems.length) {
        viewerDetail.textContent = `该日期没有已保存的${state.viewerMode === 'nutrition' ? '营养组' : '训练组'}。`;
        return;
      }
      const item =
        state.viewerItems.find((candidate) => String(candidate.id) === viewerSelect.value) ??
        state.viewerItems[0];
      if (state.viewerMode === 'nutrition') {
        const form = document.createElement('form');
        form.className = 'nutrition-editor';
        form.dataset.kind = String(item.kind ?? 'manual');
        const title = document.createElement('h2');
        title.textContent = String(item.name ?? '营养组');
        const metrics = document.createElement('div');
        metrics.className = 'nutrition-metrics';
        for (const [label, name, value] of [
          ['总摄入热量 kcal', 'calories_kcal', item.total_kcal ?? item.calories_kcal],
          ['蛋白质 g', 'protein_g', item.protein_g],
          ['碳水 g', 'carbs_g', item.carbs_g],
          ['脂肪 g', 'fat_g', item.fat_g],
        ] as const)
          metrics.append(nutritionField(document, label, name, value));
        form.append(title, metrics);
        if (item.kind === 'meal') {
          const foods = document.createElement('div');
          foods.className = 'nutrition-item-list';
          const addFood = (food: JsonObject = {}) => {
            const row = document.createElement('div');
            row.className = 'nutrition-item';
            for (const [label, name, value, type] of [
              ['食物', 'name', food.name, 'text'],
              ['份量', 'portion', food.portion, 'text'],
              ['热量 kcal', 'calories_kcal', food.calories_kcal ?? 0, 'number'],
              ['蛋白质 g', 'protein_g', food.protein_g ?? 0, 'number'],
              ['碳水 g', 'carbs_g', food.carbs_g ?? 0, 'number'],
              ['脂肪 g', 'fat_g', food.fat_g ?? 0, 'number'],
            ] as const)
              row.append(nutritionField(document, label, name, value, type));
            row.append(button(document, '移除', () => row.remove(), true));
            foods.append(row);
          };
          array(item.items).forEach(addFood);
          if (!foods.children.length) addFood();
          form.append(
            foods,
            button(document, '添加食物', () => addFood()),
          );
        } else {
          form.append(nutritionField(document, '备注', 'note', item.note, 'textarea'));
        }
        const save = button(document, '保存营养组', () => {
          const patch: JsonObject = {};
          if (item.kind === 'meal') {
            patch.items = Array.from(form.querySelectorAll<HTMLElement>('.nutrition-item')).map(
              (row) => ({
                name: row.querySelector<HTMLInputElement>('[name="name"]')?.value.trim() ?? '',
                portion:
                  row.querySelector<HTMLInputElement>('[name="portion"]')?.value.trim() ?? '',
                calories_kcal: Number(
                  row.querySelector<HTMLInputElement>('[name="calories_kcal"]')?.value ?? 0,
                ),
                protein_g: Number(
                  row.querySelector<HTMLInputElement>('[name="protein_g"]')?.value ?? 0,
                ),
                carbs_g: Number(
                  row.querySelector<HTMLInputElement>('[name="carbs_g"]')?.value ?? 0,
                ),
                fat_g: Number(row.querySelector<HTMLInputElement>('[name="fat_g"]')?.value ?? 0),
              }),
            );
          } else {
            for (const name of ['calories_kcal', 'protein_g', 'carbs_g', 'fat_g']) {
              const value = form.querySelector<HTMLInputElement>(`[name="${name}"]`)?.value ?? '';
              patch[name] = value === '' ? null : Number(value);
            }
            patch.note =
              form.querySelector<HTMLTextAreaElement>('[name="note"]')?.value.trim() ?? '';
          }
          handlers?.onNutrition('save', item, patch);
        });
        const remove = button(
          document,
          '删除该记录',
          () => handlers?.onNutrition('delete', item),
          true,
        );
        form.append(save, remove);
        viewerDetail.replaceChildren(form);
      }
    },
    renderAudit(items) {
      simpleList(
        'data-audit-list',
        'data-audit-count',
        items,
        '文件与数据库引用一致',
        (item) => `${String(item.kind ?? '文件')} · ${String(item.name ?? '')}`,
        (item) =>
          item.orphan ? [button(document, '删除', () => handlers?.onAuditDelete(item), true)] : [],
      );
    },
    renderQuarantine(items) {
      simpleList(
        'quarantined-list',
        'quarantined-count',
        items,
        '没有被隔离的未确认训练',
        (item) => String(item.name ?? ''),
        (item) => {
          const actions = [
            button(document, '只读预览', () => handlers?.onQuarantine('preview', item)),
          ];
          if (item.recoverable)
            actions.push(
              button(document, '载入编辑', () => handlers?.onQuarantine('restore', item)),
            );
          if (!item.dismissed)
            actions.push(
              button(document, '不再提醒', () => handlers?.onQuarantine('dismiss', item)),
            );
          actions.push(
            button(document, '永久删除', () => handlers?.onQuarantine('delete', item), true),
          );
          return actions;
        },
      );
    },
    renderRecovery(items) {
      simpleList(
        'recovery-points-list',
        'recovery-points-count',
        items,
        '暂无重置前恢复点',
        (item) => String(item.name ?? ''),
        (item) => [
          button(document, '下载', () => handlers?.onRecovery('download', item)),
          button(document, '删除', () => handlers?.onRecovery('delete', item), true),
        ],
      );
    },
    renderResetResult(result, onRetry) {
      const failed = array(result.steps).filter((step) => step.error);
      if (!failed.length) return;
      status.append(
        button(document, '重试失败项目', () =>
          onRetry(failed.map((step) => String(step.key)).filter(Boolean)),
        ),
      );
    },
    status(text = '', tone = '') {
      status.textContent = text;
      status.className = `modal-status${tone ? ` ${tone}` : ''}`;
    },
    previewPlan(plan) {
      previewedPlan = plan;
      required<HTMLElement>(document, '#plan-preview-title').textContent = String(
        plan.filename ?? '训练计划',
      );
      setSanitizedMarkdown(required(document, '#plan-preview-body'), String(plan.content ?? ''));
      required(document, '#plan-preview').classList.add('show');
    },
    previewRecord(data) {
      const record =
        data.record && typeof data.record === 'object' ? (data.record as JsonObject) : {};
      required<HTMLElement>(document, '#record-preview-title').textContent = String(
        record.name ?? '训练记录',
      );
      required<HTMLElement>(document, '#record-preview-body').textContent =
        `日期：${String(data.date ?? '--')}\n名称：${String(record.name ?? '--')}\n训练科目：${String(record.sport ?? '--')}`;
      required(document, '#record-preview').classList.add('show');
    },
    backupInput() {
      return required(document, '#backup-file');
    },
    resetConfirmation() {
      return required<HTMLInputElement>(document, '#reset-confirmation').value.trim();
    },
    selectedRecords() {
      return selected('records-list');
    },
    selectedPlans() {
      return selected('plans-list');
    },
  };
}
