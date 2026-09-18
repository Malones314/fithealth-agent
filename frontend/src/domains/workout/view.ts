import type { WorkoutDomainState, WorkoutSegment, WorkoutSnapshot } from './types';

export interface WorkoutViewHandlers {
  onConfirm(): void;
  onDiscard(): void;
  onClear(): void;
  onUndo(): void;
  onRestore(): void;
  onRename(): void;
  onMerge(): void;
  onDraftChanged(workout: WorkoutSnapshot): void;
}

export interface WorkoutView {
  bind(handlers: WorkoutViewHandlers): () => void;
  render(state: WorkoutDomainState): void;
  setBusy(busy: boolean): void;
  activeEditor(): boolean;
  setVisible(visible: boolean): void;
}

function required<T extends Element>(document: Document, selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Workout element not found: ${selector}`);
  return element;
}

export function formatDuration(value: unknown): string {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  if (seconds >= 3600)
    return `${Math.floor(seconds / 3600)}h${Math.floor((seconds % 3600) / 60)}m${seconds % 60}s`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m${seconds % 60}s`;
  return `${seconds}s`;
}

function segmentCard(
  document: Document,
  segment: WorkoutSegment,
  selected: Set<number>,
  onChange: (field: string, value: unknown) => void,
): HTMLElement {
  const index = Number(segment.index || 0);
  const card = document.createElement('div');
  const rest = segment.segment_type === 'set_rest' || segment.is_rest;
  const selectable = !rest && segment.segment_type === 'set_active';
  card.className = `set-card${rest ? ' rest' : segment.segment_type === 'set_active' ? '' : ' lap'}`;
  card.dataset.index = String(index);
  if (selected.has(index)) card.classList.add('selected');
  const top = document.createElement('div');
  top.className = 'set-card-top';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'set-select';
  checkbox.checked = selected.has(index);
  checkbox.disabled = !selectable;
  checkbox.setAttribute('aria-label', `选择第 ${index} 组`);
  const idx = document.createElement('span');
  idx.className = 'set-idx';
  idx.textContent = `#${index}`;
  const name = document.createElement('span');
  name.className = rest ? 'rest-label' : 'set-name';
  name.textContent = rest ? '组间休息' : String(segment.category || `第 ${index} 段`);
  top.append(...(selectable ? [checkbox] : []), idx, name);
  const stats = document.createElement('div');
  stats.className = 'set-stats';
  const values = rest
    ? [formatDuration(segment.duration_s)]
    : [
        segment.weight_kg != null ? `${segment.weight_kg} kg` : '',
        segment.repetitions != null ? `${segment.repetitions} 次` : '',
        segment.distance_m ? `${Number(segment.distance_m).toFixed(0)} m` : '',
        formatDuration(segment.duration_s),
      ].filter(Boolean);
  values.forEach((value) => {
    const stat = document.createElement('span');
    stat.className = 'set-stat';
    stat.textContent = value;
    stats.append(stat);
  });
  card.append(top, stats);
  if (!rest && segment.segment_type === 'set_active') {
    const editor = document.createElement('div');
    editor.className = 'set-editor';
    for (const [field, label, value] of [
      ['weight_kg', '重量', segment.weight_kg],
      ['repetitions', '次数', segment.repetitions],
      ['duration_s', '时长', segment.duration_s],
    ] as const) {
      const wrap = document.createElement('label');
      wrap.className = 'editor-field';
      wrap.textContent = label;
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'editor-input';
      input.value = value == null ? '' : String(value);
      input.addEventListener('input', () =>
        onChange(field, input.value === '' ? null : Number(input.value)),
      );
      wrap.append(input);
      editor.append(wrap);
    }
    card.append(editor);
  }
  return card;
}

export function createWorkoutView(document: Document): WorkoutView {
  const list = required<HTMLElement>(document, '#workout-list');
  const count = required<HTMLElement>(document, '#set-count');
  const actions = required<HTMLElement>(document, '#workout-actions');
  const toolbar = required<HTMLElement>(document, '#editor-toolbar');
  const selection = required<HTMLElement>(document, '#editor-selection');
  const time = required<HTMLElement>(document, '#workout-time');
  const noteWrap = required<HTMLElement>(document, '#workout-note-wrap');
  const note = required<HTMLTextAreaElement>(document, '#workout-note');
  const editorArea = required<HTMLElement>(document, '#training-editor-area');
  const buttons = {
    confirm: required<HTMLButtonElement>(document, '#btn-confirm'),
    discard: required<HTMLButtonElement>(document, '#btn-discard'),
    clear: required<HTMLButtonElement>(document, '#btn-clear'),
    undo: required<HTMLButtonElement>(document, '#btn-undo'),
    restore: required<HTMLButtonElement>(document, '#btn-restore-source'),
    rename: required<HTMLButtonElement>(document, '#btn-rename'),
    merge: required<HTMLButtonElement>(document, '#btn-merge'),
  };
  let current: WorkoutDomainState | null = null;
  let draftChanged: WorkoutViewHandlers['onDraftChanged'] = () => undefined;

  const listeners: Array<[EventTarget, string, EventListener]> = [];
  const listen = (target: EventTarget, event: string, listener: EventListener) => {
    target.addEventListener(event, listener);
    listeners.push([target, event, listener]);
  };

  return {
    bind(handlers) {
      draftChanged = handlers.onDraftChanged;
      listen(buttons.confirm, 'click', handlers.onConfirm);
      listen(buttons.discard, 'click', handlers.onDiscard);
      listen(buttons.clear, 'click', handlers.onClear);
      listen(buttons.undo, 'click', handlers.onUndo);
      listen(buttons.restore, 'click', handlers.onRestore);
      listen(buttons.rename, 'click', handlers.onRename);
      listen(buttons.merge, 'click', handlers.onMerge);
      listen(note, 'input', () => {
        if (!current?.draft) return;
        current.draft.note = note.value;
        draftChanged(current.draft);
      });
      listen(list, 'change', (event) => {
        const checkbox = event.target as HTMLInputElement;
        const card = checkbox.closest<HTMLElement>('.set-card');
        if (!current || checkbox.type !== 'checkbox' || !card) return;
        const index = Number(card.dataset.index);
        if (checkbox.checked) current.selected.add(index);
        else current.selected.delete(index);
        card.classList.toggle('selected', checkbox.checked);
        selection.textContent = `已选择 ${current.selected.size} 组`;
        buttons.merge.disabled = current.selected.size < 2;
        buttons.rename.disabled = current.selected.size === 0;
      });
      return () =>
        listeners
          .splice(0)
          .forEach(([target, event, listener]) => target.removeEventListener(event, listener));
    },
    render(state) {
      current = state;
      document.documentElement.dataset.workoutStatus = state.status;
      const workout = state.draft;
      const sets = workout?.sets ?? [];
      list.replaceChildren();
      count.textContent = `${sets.length} 组`;
      selection.textContent = `已选择 ${state.selected.size} 组`;
      buttons.merge.disabled = state.selected.size < 2;
      buttons.rename.disabled = state.selected.size === 0;
      if (!workout) {
        const empty = document.createElement('div');
        empty.className = 'workout-empty';
        empty.textContent = '上传 .fit 文件后将在此显示训练组';
        list.append(empty);
        actions.classList.remove('show');
        toolbar.classList.remove('show');
        noteWrap.classList.remove('show');
        time.textContent = '训练时间：--';
        return;
      }
      sets.forEach((segment) => {
        list.append(
          segmentCard(document, segment, state.selected, (field, value) => {
            segment[field] = value;
            state.status = 'editing';
            draftChanged(workout);
          }),
        );
      });
      actions.classList.add('show');
      toolbar.classList.add('show');
      noteWrap.classList.add('show');
      note.value = String(workout.note || '');
      const start = sets.map((item) => item.start_time).find(Boolean);
      time.textContent = `训练时间（北京时间）：${start ? new Date(start).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '--'}`;
      buttons.undo.disabled = !state.editHistory.can_undo;
      buttons.restore.disabled = !state.editHistory.can_restore_parsed_source;
      buttons.confirm.textContent = state.mode === 'saved' ? '保存训练记录修改' : '确认并保存训练';
      buttons.discard.textContent = state.mode === 'saved' ? '放弃记录修改' : '丢弃';
    },
    setBusy(busy) {
      buttons.confirm.disabled = busy;
      buttons.discard.disabled = busy;
      buttons.clear.disabled = busy;
      buttons.confirm.textContent = busy
        ? '保存中...'
        : current?.mode === 'saved'
          ? '保存训练记录修改'
          : '确认并保存训练';
    },
    activeEditor: () => Boolean(document.activeElement?.closest('.set-editor')),
    setVisible(visible) {
      editorArea.hidden = !visible;
    },
  };
}
