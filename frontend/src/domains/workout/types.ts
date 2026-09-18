import type { JsonObject } from '../../shared/types';

export interface WorkoutSegment extends JsonObject {
  index?: number;
  segment_type?: string;
  is_rest?: boolean;
  category?: string;
  repetitions?: number;
  weight_kg?: number;
  duration_s?: number;
  distance_m?: number;
  avg_speed_mps?: number;
  avg_hr?: number;
  max_hr?: number;
  calories?: number;
  start_time?: string;
}

export interface WorkoutSnapshot extends JsonObject {
  name?: string;
  sport?: string;
  sets?: WorkoutSegment[];
  session?: JsonObject;
  note?: string;
}

export interface WorkoutConfirmation extends JsonObject {
  workout_id?: string;
  version?: number;
  confirmation_token?: string;
}

export interface WorkoutDomainState {
  snapshot: WorkoutSnapshot | null;
  draft: WorkoutSnapshot | null;
  revision?: number;
  mode: 'none' | 'pending' | 'saved';
  savedRecordId?: string;
  status: 'idle' | 'loading' | 'editing' | 'saving' | 'conflict' | 'error';
  confirmation: WorkoutConfirmation | null;
  selected: Set<number>;
  editHistory: { can_undo: boolean; can_restore_parsed_source: boolean };
  error?: string;
}
