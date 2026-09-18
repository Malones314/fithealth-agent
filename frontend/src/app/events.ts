import type { JsonObject } from '../shared/types';

export interface AppEventMap {
  startup: undefined;
  refresh: undefined;
  destroy: undefined;
  'workout:loaded': JsonObject;
  'workout:cleared': undefined;
  'workout:refresh': undefined;
  'workout:edit-saved': { recordId: string; showNotice?: boolean };
  'workout:visibility': { visible: boolean };
  'workout:status': {
    hasWorkout: boolean;
    mode: 'none' | 'pending' | 'saved';
    recordId?: string;
  };
  'chat:message': { role: 'user' | 'bot'; text: string };
  'chat:submit-plan': { content: string; subject: string };
  'checkin:food': JsonObject;
  'checkin:open': { day?: string };
  'health:refresh': undefined;
  'data:refresh': undefined;
  'data:view-mode': { mode: 'training' | 'nutrition'; day?: string };
  'session:changed': undefined;
  'health:daily': { day: string; resolve: (value: unknown) => void };
  'health:range': { params: string; resolve: (value: unknown) => void };
  'health:sleep': { day: string; resolve: (value: unknown) => void };
  'health:raw-audit': { resolve: (value: unknown) => void };
  'health:import-details': { id: string; resolve: (value: unknown) => void };
  'health:delete-import': { id: string; resolve: (value: unknown) => void };
  'health:delete-raw-orphan': { name: string; resolve: (value: unknown) => void };
}

export const appEvents = createEventBus();

export type AppEvent = keyof AppEventMap;

export interface AppEventBus {
  on<K extends AppEvent>(event: K, listener: (payload: AppEventMap[K]) => void): () => void;
  emit<K extends AppEvent>(
    event: K,
    ...payload: AppEventMap[K] extends undefined ? [] : [AppEventMap[K]]
  ): void;
}

export function createEventBus(): AppEventBus {
  const listeners = new Map<AppEvent, Set<(payload: never) => void>>();
  return {
    on(event, listener) {
      const set = listeners.get(event) ?? new Set<(payload: never) => void>();
      set.add(listener as (payload: never) => void);
      listeners.set(event, set);
      return () => set.delete(listener as (payload: never) => void);
    },
    emit(event, ...payload) {
      listeners.get(event)?.forEach((listener) => listener(payload[0] as never));
    },
  };
}
