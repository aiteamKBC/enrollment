const DOWNLOAD = '/learner_api/mobile/download/android/';
const PART_BYTES = 1024 * 1024;
const MAX_BYTES = 250 * 1024 * 1024;

export async function fetchVerifiedInstaller(
  onProgress: (percent: number) => void, signal: AbortSignal,
  fetcher: typeof fetch = fetch,
  digest: (data: ArrayBuffer) => Promise<ArrayBuffer> = data => crypto.subtle.digest('SHA-256', data),
): Promise<Blob> {
  const parts: ArrayBuffer[] = [];
  let offset = 0, total = 0, etag = '', expectedHash = '';
  do {
    let part: ArrayBuffer | undefined;
    for (let attempt = 0; attempt < 3; attempt++) {
      signal.throwIfAborted();
      const request = new AbortController();
      const cancel = () => request.abort();
      signal.addEventListener('abort', cancel, { once: true });
      const timer = setTimeout(cancel, 30000);
      try {
        const response = await fetcher(DOWNLOAD, {
          credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal,
          headers: {Accept: 'application/vnd.android.package-archive',
            Range: `bytes=${offset}-${offset + PART_BYTES - 1}`, ...(etag ? {'If-Range': etag} : {})},
        });
        if ([401, 403].includes(response.status)) throw new Error('Sign in again before downloading the app.');
        if (response.status >= 500 || response.status === 429) throw new Error('The download service is busy. Please retry.');
        if (response.status !== 206) throw new Error('The download changed or is unavailable. Please start again.');
        const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get('Content-Range') || '');
        // Django gzip marks the transport ETag weak. Fetch decodes the body;
        // the full SHA-256 below still verifies the original APK bytes.
        const hash = response.headers.get('X-APK-SHA256') || '', nextEtag = (response.headers.get('ETag') || '').replace(/^W\//, '');
        if (!match || Number(match[1]) !== offset || !/^[a-f0-9]{64}$/.test(hash)
          || nextEtag !== '"' + hash + '"' || (etag && nextEtag !== etag)
          || Number(match[3]) <= 0 || Number(match[3]) > MAX_BYTES
          || (total && total !== Number(match[3]))
          || Number(match[2]) !== Math.min(offset + PART_BYTES, Number(match[3])) - 1) {
          throw new Error('The download could not be verified. Please start again.');
        }
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength !== Number(match[2]) - offset + 1) throw new Error('The connection stopped during download. Please retry.');
        total = Number(match[3]); expectedHash = hash; etag = nextEtag; part = bytes;
        break;
      } catch (error) {
        signal.throwIfAborted();
        if (attempt === 2 || (error instanceof Error && /Sign in|changed|verified/.test(error.message))) throw error;
      } finally {
        clearTimeout(timer); signal.removeEventListener('abort', cancel);
      }
    }
    if (!part) throw new Error('Download incomplete. Please retry.');
    parts.push(part); offset += part.byteLength;
    onProgress(Math.floor(95 * offset / total));
  } while (offset < total);
  signal.throwIfAborted();
  const blob = new Blob(parts, {type: 'application/vnd.android.package-archive'});
  const actual = Array.from(new Uint8Array(await digest(await blob.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('');
  signal.throwIfAborted();
  if (actual !== expectedHash) throw new Error('The app file is incomplete or damaged. Please download again.');
  onProgress(100);
  return blob;
}

