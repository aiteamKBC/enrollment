import { fireEvent, screen } from '@testing-library/react';

export function editorGroup({ name }: { name: string }) {
  const field = Array.from(document.querySelectorAll('fieldset')).find((item) => item.getAttribute('aria-label') === name);
  if (!field) throw new Error(`Missing editor: ${name}`);
  const ancestors: HTMLDetailsElement[] = [];
  for (let node = field.parentElement; node; node = node.parentElement) if (node instanceof HTMLDetailsElement && !node.open) ancestors.unshift(node);
  for (const detail of ancestors) fireEvent.click(detail.querySelector('summary')!);
  return screen.getByRole('group', { name });
}
