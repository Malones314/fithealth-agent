export function escapeHtml(value: unknown): string {
  const text = String(value ?? '');
  return text.replace(/[&<>'"`]/g, (character) => {
    const entities: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;',
      '`': '&#96;',
    };
    return entities[character];
  });
}

export function query<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) throw new Error(`Required element not found: ${selector}`);
  return element;
}

export function readFormValue(form: HTMLFormElement, name: string): string {
  const field = form.elements.namedItem(name);
  if (
    !(
      field instanceof HTMLInputElement ||
      field instanceof HTMLSelectElement ||
      field instanceof HTMLTextAreaElement
    )
  ) {
    throw new Error(`Form field not found: ${name}`);
  }
  return field.value;
}

export function setBusy(element: HTMLButtonElement, busy: boolean): void {
  element.disabled = busy;
  element.setAttribute('aria-busy', String(busy));
}
