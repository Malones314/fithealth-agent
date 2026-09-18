import type { AppState } from '../app/app-state';
import type { AppEventBus } from '../app/events';
import type { AppShell } from '../app/shell';
import type { OperationRegistry } from '../shared/operations';

export interface DomainContext {
  state: AppState;
  events: AppEventBus;
  shell: AppShell;
  operations: OperationRegistry;
  document: Document;
}

export interface DomainController {
  start(): void;
  stop?(): void;
}
