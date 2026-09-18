import { createAppState } from './app-state';
import { appEvents } from './events';
import { createSessionController } from '../domains/session/controller';
import { createWorkoutController } from '../domains/workout/controller';
import { createUploadsController } from '../domains/uploads/controller';
import { createChatController } from '../domains/chat/controller';
import { createHealthController } from '../domains/health/controller';
import { createCheckinController } from '../domains/checkin/controller';
import { createDataManagementController } from '../domains/data-management/controller';
import { shell } from './shell';
import { operations } from '../shared/operations';
import type { DomainController } from '../domains/domain-factory';
import { sessionState } from '../domains/session/state';
import { installLayout } from './layout';

let started = false;
let activeControllers: DomainController[] = [];
let activeEvents: typeof appEvents | null = null;
let disposeLayout: (() => void) | null = null;

export function bootstrap(): void {
  if (started) return;
  started = true;
  const state = createAppState(sessionState);
  const events = appEvents;
  activeEvents = events;
  const context = { state, events, shell: shell(), operations, document };
  disposeLayout = installLayout(document, window);
  activeControllers = [
    createSessionController(context),
    createWorkoutController(context),
    createUploadsController(context),
    createChatController(context),
    createHealthController(context),
    createCheckinController(context),
    createDataManagementController(context),
  ];
  activeControllers.forEach((controller) => controller.start());
  events.emit('startup');
}

export function destroy(): void {
  if (!started) return;
  activeEvents?.emit('destroy');
  activeControllers.forEach((controller) => controller.stop?.());
  activeControllers = [];
  activeEvents = null;
  disposeLayout?.();
  disposeLayout = null;
  operations.cancelAll();
  started = false;
}
