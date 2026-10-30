/**
 * The small formatting language of the wizard's editable text — a Markdown
 * subset, chosen so the builder's toolbar can write it and a person can still
 * read it in the box:
 *
 *   ## Heading            ### Smaller heading
 *   **bold**   *italic*   [link text](https://… or mailto:…)
 *   - bullet item         1. numbered item
 *
 * A blank line starts a new paragraph; a single line break stays a line break.
 * Links only ever point at http(s) or mailto addresses — anything else is shown
 * as plain text — and nothing here produces HTML.
 */

export type RichInline =
  | { type: 'text'; text: string }
  | { type: 'break' }
  | { type: 'bold'; children: RichInline[] }
  | { type: 'italic'; children: RichInline[] }
  | { type: 'link'; href: string; children: RichInline[] };

export type RichBlock =
  | { type: 'heading'; level: 2 | 3; children: RichInline[] }
  | { type: 'paragraph'; children: RichInline[] }
  | { type: 'list'; ordered: boolean; items: RichInline[][] };

const SAFE_HREF = /^(https?:\/\/|mailto:)[^\s]+$/i;

const PATTERNS: { re: RegExp; make: (m: RegExpExecArray) => RichInline }[] = [
  {
    re: /\[([^\]]+)\]\(([^)\s]+)\)/,
    make: (m) => (SAFE_HREF.test(m[2])
      ? { type: 'link', href: m[2], children: parseInline(m[1]) }
      : { type: 'text', text: m[1] }),
  },
  { re: /\*\*([^*]+(?:\*(?!\*)[^*]*)*)\*\*/, make: (m) => ({ type: 'bold', children: parseInline(m[1]) }) },
  { re: /\*([^*\s][^*]*?)\*/, make: (m) => ({ type: 'italic', children: parseInline(m[1]) }) },
];

export function parseInline(text: string): RichInline[] {
  const out: RichInline[] = [];
  let rest = text;
  while (rest) {
    let best: { index: number; length: number; node: RichInline } | null = null;
    for (const { re, make } of PATTERNS) {
      const m = re.exec(rest);
      if (m && (best === null || m.index < best.index)) best = { index: m.index, length: m[0].length, node: make(m) };
    }
    if (!best) {
      out.push(...textWithBreaks(rest));
      break;
    }
    if (best.index > 0) out.push(...textWithBreaks(rest.slice(0, best.index)));
    out.push(best.node);
    rest = rest.slice(best.index + best.length);
  }
  return out;
}

function textWithBreaks(text: string): RichInline[] {
  const out: RichInline[] = [];
  text.split('\n').forEach((part, i) => {
    if (i > 0) out.push({ type: 'break' });
    if (part) out.push({ type: 'text', text: part });
  });
  return out;
}

const BULLET = /^\s*[-*]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

export function parseRich(source: string): RichBlock[] {
  const blocks: RichBlock[] = [];
  const chunks = source.replace(/\r\n?/g, '\n').split(/\n\s*\n/);
  for (const chunk of chunks) {
    const lines = chunk.split('\n').filter((l) => l.trim() !== '');
    if (lines.length === 0) continue;
    let paragraph: string[] = [];
    const flush = () => {
      if (paragraph.length) blocks.push({ type: 'paragraph', children: parseInline(paragraph.join('\n')) });
      paragraph = [];
    };
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const heading = /^(#{2,3})\s+(.*)$/.exec(line.trim());
      if (heading) {
        flush();
        blocks.push({ type: 'heading', level: heading[1].length === 2 ? 2 : 3, children: parseInline(heading[2]) });
        i += 1;
        continue;
      }
      if (BULLET.test(line) || NUMBERED.test(line)) {
        flush();
        const ordered = NUMBERED.test(line);
        const marker = ordered ? NUMBERED : BULLET;
        const items: RichInline[][] = [];
        while (i < lines.length && marker.test(lines[i])) {
          items.push(parseInline(lines[i].replace(marker, '')));
          i += 1;
        }
        blocks.push({ type: 'list', ordered, items });
        continue;
      }
      paragraph.push(line.trim());
      i += 1;
    }
    flush();
  }
  return blocks;
}

/** The text with its formatting removed — for the PDF, which prints plain text. */
export function plainText(source: string): string {
  const flatten = (nodes: RichInline[]): string =>
    nodes.map((n) => (n.type === 'text' ? n.text : n.type === 'break' ? '\n' : flatten(n.children))).join('');
  return parseRich(source)
    .map((b) => (b.type === 'list' ? b.items.map((it) => `• ${flatten(it)}`).join('\n') : flatten(b.children)))
    .join('\n\n');
}
