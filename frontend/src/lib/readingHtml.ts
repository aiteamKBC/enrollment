/** Decode the escaped markup retained by older reading editors before it is
 * sanitized. Keep the same line-break handling as the existing activity player. */
export function normalizeReadingHtml(value: string): string {
  if (!/&lt;\/?[a-z][a-z0-9]*(&gt;|\s)/i.test(value)) return value;
  const textarea = document.createElement('textarea');
  textarea.innerHTML = value.replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/div>\s*<div>/gi, '\n').replace(/<\/?div>/gi, '');
  return textarea.value;
}
