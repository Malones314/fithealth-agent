import type { WorkoutDomainState, WorkoutSnapshot } from './types';

export function createWorkoutState(): WorkoutDomainState {
  return {
    snapshot: null,
    draft: null,
    status: 'idle',
    mode: 'none',
    confirmation: null,
    selected: new Set(),
    editHistory: { can_undo: false, can_restore_parsed_source: false },
  };
}

export function cloneWorkout(workout: WorkoutSnapshot): WorkoutSnapshot {
  return structuredClone(workout);
}

export function loadWorkoutSnapshot(
  state: WorkoutDomainState,
  workout: WorkoutSnapshot,
  mode: WorkoutDomainState['mode'] = 'pending',
): void {
  state.selected.clear();
  state.snapshot = cloneWorkout(workout);
  state.draft = cloneWorkout(workout);
  state.status = 'editing';
  state.mode = mode;
  state.error = undefined;
}

export function beginWorkoutDraft(
  state: WorkoutDomainState,
  draft: WorkoutSnapshot | null,
): WorkoutDomainState {
  return { ...state, draft: draft ? cloneWorkout(draft) : null, status: 'editing' };
}

export function clearWorkout(state: WorkoutDomainState): void {
  state.snapshot = null;
  state.draft = null;
  state.confirmation = null;
  state.mode = 'none';
  state.savedRecordId = undefined;
  state.revision = undefined;
  state.selected.clear();
  state.status = 'idle';
}
