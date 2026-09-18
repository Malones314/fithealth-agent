import { describe, expect, it } from 'vitest';
import { renderMarkdown, sanitizeHtml, setSanitizedHtml } from './sanitize';

describe('safe rich text boundary', () => {
  it('removes scripts, event handlers, javascript URLs and restricted form tags', () => {
    const html = sanitizeHtml(
      '<script>alert(1)</script><img src="x" onerror="alert(2)"><a href="javascript:alert(3)">bad</a><form><input value="secret"></form><strong>ok</strong>',
    );
    expect(html).not.toMatch(/script|onerror|javascript:|form|input/i);
    expect(html).toContain('<strong>ok</strong>');
  });

  it('renders markdown through the single sanitizer entry point', () => {
    const html = renderMarkdown(
      '# hi\n\n[click](javascript:alert(1))\n\n<img src=x onerror=alert(1)>',
    );
    expect(html).toContain('<h1>hi</h1>');
    expect(html).not.toMatch(/javascript:|onerror|<script/i);
  });

  it('writes runtime HTML only after sanitizing it', () => {
    const target = document.createElement('div');
    setSanitizedHtml(target, '<span>ok</span><img src=x onerror=alert(1)>');
    expect(target.innerHTML).toContain('<span>ok</span>');
    expect(target.innerHTML).not.toContain('onerror');
  });
});
