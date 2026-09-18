import { describe, expect, it } from 'vitest';
import { isIsoDate, validateUpload } from './validation';

describe('shared validation', () => {
  it('validates dates and upload types', () => {
    expect(isIsoDate('2026-09-11')).toBe(true);
    expect(isIsoDate('2026-99-99')).toBe(false);
    expect(() =>
      validateUpload(new File(['x'], 'a.txt', { type: 'text/plain' }), ['.fit']),
    ).toThrow('不支持');
    expect(() => validateUpload(new File(['x'], 'a.fit'), ['.fit'])).not.toThrow();
  });
});
