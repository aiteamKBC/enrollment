/** A separate external listening route, never an audio stream or completion signal. */
export function audioAlternativeUrl(value) {
  if (typeof value !== 'string' || value.length > 2048 || [...value].some(char => char.codePointAt(0) <= 32 || char.codePointAt(0) === 127)) return null;
  try {
    const url = new globalThis.URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return null;
    let id = null;
    if (url.hostname === 'youtu.be' && /^\/[A-Za-z0-9_-]{11}$/.test(url.pathname)) id = url.pathname.slice(1);
    else if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(url.hostname) && url.pathname === '/watch') {
      const ids = url.searchParams.getAll('v');
      if (ids.length === 1) id = ids[0];
    }
    return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? 'https://www.youtube.com/watch?v=' + id : null;
  } catch { return null; }
}

/** Stop and persist the native visit before leaving; failure must not open the provider. */
export async function openAudioAlternative(value, { pause, persist, open }) {
  const url = audioAlternativeUrl(value);
  if (!url) return false;
  pause();
  await persist();
  await open(url);
  return true;
}
