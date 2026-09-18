import { describe, expect, it } from 'vitest';

describe('frontend build environment', () => {
  it('provides a browser DOM', () => {
    const element = document.createElement('main');
    element.id = 'app';
    expect(element.id).toBe('app');
  });
});
