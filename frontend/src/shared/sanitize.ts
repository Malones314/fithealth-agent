import DOMPurify from 'dompurify';
import { marked } from 'marked';

const FORBIDDEN_TAGS = [
  'style',
  'form',
  'input',
  'button',
  'textarea',
  'select',
  'option',
  'iframe',
  'object',
  'embed',
];

export function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: FORBIDDEN_TAGS,
    FORBID_ATTR: ['style', 'onerror', 'onclick', 'onload', 'onmouseover'],
    ALLOW_UNKNOWN_PROTOCOLS: false,
  });
}

export function renderMarkdown(markdown: string): string {
  return sanitizeHtml(marked.parse(markdown, { async: false }) as string);
}

export function setSanitizedMarkdown(element: Element, markdown: string): void {
  element.innerHTML = renderMarkdown(markdown);
}

export function setSanitizedHtml(element: Element, html: string): void {
  element.innerHTML = sanitizeHtml(html);
}
