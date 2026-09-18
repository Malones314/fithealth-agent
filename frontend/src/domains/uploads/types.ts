import type { JsonObject, UploadResult } from '../../shared/types';

export interface ActivityChoice extends JsonObject {
  zip: string;
  name: string;
}

export interface UploadDomainState {
  active: boolean;
  status: 'idle' | 'uploading' | 'done' | 'error';
  result: UploadResult | null;
  error?: string;
  zipFiles: Map<string, File>;
  activities: ActivityChoice[];
  activeActivity?: string;
  pendingFoodImage?: File;
}
