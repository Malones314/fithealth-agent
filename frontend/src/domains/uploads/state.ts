import type { ActivityChoice, UploadDomainState } from './types';

export function createUploadsState(): UploadDomainState {
  return {
    active: false,
    status: 'idle',
    result: null,
    zipFiles: new Map(),
    activities: [],
  };
}

export function activityKey(activity: ActivityChoice): string {
  return `${activity.zip}\u0000${activity.name}`;
}

export function setActivities(
  state: UploadDomainState,
  files: File[],
  activities: ActivityChoice[],
): void {
  state.zipFiles = new Map(files.map((file) => [file.name, file]));
  state.activities = activities.filter((activity) => state.zipFiles.has(activity.zip));
}

export function completeActivity(state: UploadDomainState, activity: ActivityChoice): void {
  const key = activityKey(activity);
  state.activities = state.activities.filter((item) => activityKey(item) !== key);
  state.activeActivity = undefined;
}
