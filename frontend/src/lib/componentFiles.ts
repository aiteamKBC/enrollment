// One component, several attachments, in the order the author uploaded them.
//
// Reading Material, PowerPoint and Podcast each used to hold exactly one file,
// spread across per-type keys (`resourceUrl`, `presentationUrl`, `podcastUrl`)
// plus the shared `uploadedFile*` quartet. Those keys stay — every existing
// reader still finds the first file exactly where it has always been — and this
// list sits beside them in `settings.componentFiles` as the full, ordered set.
//
// Position is the index: 0 is the first file uploaded, 1 the second, and so on.
// There is no stored index field, because a stored one and the array order
// would eventually disagree and nothing could say which was right.
//
// The shape is deliberately identical to `AssignmentTopicResource` — an
// assignment already carries several files per topic, and an attachment should
// not describe itself differently depending on which component holds it.

export interface ComponentFile {
  fileName: string;
  url: string;
  size: number;
  contentType: string;
}

/** Our own upload path, or a plain web link. Anything else is not an attachment. */
export function safeComponentFileUrl(url: unknown): url is string {
  return typeof url === 'string' && (/^https?:\/\//i.test(url) || /^\/curriculum_api\/curriculum\/uploads\//.test(url));
}

function readComponentFile(value: unknown): ComponentFile | null {
  if (!value || typeof value !== 'object') return null;
  const source = value as Record<string, unknown>;
  if (!safeComponentFileUrl(source.url)) return null;
  return {
    fileName: typeof source.fileName === 'string' ? source.fileName : '',
    url: source.url,
    size: Number(source.size) || 0,
    contentType: typeof source.contentType === 'string' ? source.contentType : '',
  };
}

/**
 * The stored list, as an array. Accepts the JSON string the setting actually
 * holds as well as an already-parsed array, because the authoring payload
 * round-trips through both. Unreadable storage reads as "no list", never as an
 * error: the legacy single-file keys are still there to fall back on.
 */
export function componentFiles(value: unknown): ComponentFile[] {
  let parsed = value;
  if (typeof parsed === 'string') {
    const text = parsed.trim();
    if (!text) return [];
    try { parsed = JSON.parse(text); } catch { return []; }
  }
  if (!Array.isArray(parsed)) return [];
  const seen = new Set<string>();
  const files: ComponentFile[] = [];
  for (const entry of parsed) {
    const file = readComponentFile(entry);
    // The same upload twice is one attachment; the author sees one row and
    // removing it removes the file rather than half of it.
    if (!file || seen.has(file.url)) continue;
    seen.add(file.url);
    files.push(file);
  }
  return files;
}

/** Empty for an empty list, so a component with no files stores nothing. */
export function serialiseComponentFiles(files: ComponentFile[]): string {
  return files.length ? JSON.stringify(files) : '';
}

/**
 * The ordered list for a component, reading the legacy single-file keys when no
 * list has been written yet.
 *
 * Every component authored before this existed carries its one file only in
 * those keys, and nothing re-saves a component on its own — so the fallback is
 * not a migration nicety, it is how most components will be read for a long
 * time.
 */
export function componentFileList(value: unknown, legacy: ComponentFile | null): ComponentFile[] {
  const stored = componentFiles(value);
  if (stored.length) return stored;
  return legacy && safeComponentFileUrl(legacy.url) ? [legacy] : [];
}

/** Build the fallback entry from whichever per-type keys a caller holds. */
export function legacyComponentFile(url: unknown, fileName: unknown, size: unknown, contentType: unknown): ComponentFile | null {
  if (!safeComponentFileUrl(url)) return null;
  return {
    fileName: typeof fileName === 'string' ? fileName : '',
    url,
    size: Number(size) || 0,
    contentType: typeof contentType === 'string' ? contentType : '',
  };
}
