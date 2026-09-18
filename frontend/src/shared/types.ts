export type JsonObject = Record<string, unknown>;

export type ChatRole = 'user' | 'assistant' | 'system' | 'tool';

export interface ChatMessage {
  role: ChatRole;
  content: string;
  name?: string;
}

export interface WorkoutSet {
  reps?: number;
  weight?: number;
  durationSeconds?: number;
  distanceMeters?: number;
  rpe?: number;
  [key: string]: unknown;
}

export interface WorkoutExercise {
  name: string;
  sets?: WorkoutSet[];
  [key: string]: unknown;
}

export interface Workout {
  id?: string;
  revision?: number;
  date?: string;
  sport?: string;
  exercises?: WorkoutExercise[];
  [key: string]: unknown;
}

export interface TrainingRecord {
  id: string;
  revision?: number;
  date?: string;
  record?: Workout | JsonObject;
  [key: string]: unknown;
}

export interface HealthOverview {
  date: string;
  has_data: boolean;
  available_sections?: string[];
  sleep?: JsonObject;
  heart_rate?: JsonObject;
  activity?: JsonObject;
  [key: string]: unknown;
}

export interface Checkin {
  id?: string;
  date: string;
  revision?: number;
  [key: string]: unknown;
}

export interface Plan {
  id?: string;
  draft_id?: string;
  title?: string;
  subject?: string;
  content: string;
  revision?: number;
  suggested_date?: string;
  [key: string]: unknown;
}

export interface MemoryFact {
  fact_id?: string;
  namespace: string;
  key: string;
  value: unknown;
  evidence?: string;
  status?: string;
  [key: string]: unknown;
}

export interface UploadResult extends JsonObject {
  uploaded?: boolean;
  filename?: string;
  activities?: Array<JsonObject & { zip?: string; name?: string }>;
}

export interface EditDraft<T> {
  value: T;
  baseRevision?: number;
  dirty: boolean;
}

export interface ApiError {
  status: number;
  message: string;
  serverCode?: string;
  clientCorrelationId: string;
  retryable: boolean;
  details?: unknown;
  /**
   * 请求路径（不含查询串）。只供诊断使用。
   *
   * 阶段 5 的异常上报只允许记录模块、endpoint、状态码和 clientCorrelationId，
   * 所以路径必须能从错误对象上直接拿到——否则上报处只能去翻 `details`，而那里
   * 装着服务器响应体，可能含健康数据。
   */
  endpoint?: string;
}

export interface RequestContext {
  clientCorrelationId: string;
}
