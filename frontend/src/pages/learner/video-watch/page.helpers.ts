


export function fileProbe(url: string, fileName?: string | null): string {
  const named = fileName?.trim().split(/[?#]/)[0];
  // Protected file endpoints have no extension in their path. Use the known
  // filename instead of appending the URL and hiding its extension.
  return named && /\.[a-z0-9]{1,8}$/i.test(named) ? named : url.split(/[?#]/)[0];
}
