export const MAX_UPLOAD_BYTES = 1024 * 1024 * 1024;

export function isIsoDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

export function requireIsoDate(value: string): string {
  if (!isIsoDate(value)) throw new Error(`Invalid ISO date: ${value}`);
  return value;
}

export function validateUpload(file: File, acceptedTypes: readonly string[] = []): void {
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('文件超过 1 GiB 限制');
  if (
    acceptedTypes.length > 0 &&
    !acceptedTypes.includes(file.type) &&
    !acceptedTypes.some((type) => file.name.toLowerCase().endsWith(type))
  ) {
    throw new Error(`不支持的文件类型: ${file.name}`);
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
