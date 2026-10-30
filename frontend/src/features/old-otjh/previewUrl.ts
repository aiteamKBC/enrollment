import { contentUrl } from './report';

/** Keep source access parameters while using each provider's embedded viewer. */
export function previewUrl(value: string): string {
  const valid = contentUrl(value);
  if (!valid) return '';
  const source = new URL(valid);
  const host = source.hostname.toLowerCase();
  if (host === 'drive.google.com') {
    const folder = source.pathname.match(/^\/drive\/(?:u\/\d+\/)?folders\/([\w-]+)/);
    const file = source.pathname.match(/^\/file\/d\/([\w-]+)/);
    const fileId = file?.[1] || (/^\/(open|uc)$/.test(source.pathname) ? source.searchParams.get('id') : null);
    let embed: URL | undefined;
    if (folder) {
      embed = new URL('https://drive.google.com/embeddedfolderview');
      embed.searchParams.set('id', folder[1]);
      embed.hash = 'list';
    } else if (fileId && /^[\w-]+$/.test(fileId)) embed = new URL(`https://drive.google.com/file/d/${fileId}/preview`);
    if (embed) {
      const resourceKey = source.searchParams.get('resourcekey');
      if (resourceKey) embed.searchParams.set('resourcekey', resourceKey);
      return embed.href;
    }
  }
  if (host === 'docs.google.com') {
    const doc = source.pathname.match(/^\/(document|spreadsheets|presentation)\/d\/([\w-]+)(?:\/|$)/);
    if (doc && doc[2] !== 'e') {
      source.pathname = `/${doc[1]}/d/${doc[2]}/preview`;
      return source.href;
    }
  }
  if (['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'].includes(host)) {
    const id = host === 'youtu.be' ? source.pathname.slice(1) : source.searchParams.get('v') || source.pathname.match(/^\/(?:shorts|live|embed)\/([\w-]+)/)?.[1];
    if (id && /^[\w-]+$/.test(id)) {
      const embed = new URL(`https://www.youtube.com/embed/${id}`);
      for (const key of ['start', 'end', 'list', 'index']) {
        const value = source.searchParams.get(key);
        if (value) embed.searchParams.set(key, value);
      }
      const time = source.searchParams.get('t');
      if (time && !embed.searchParams.has('start')) {
        const match = time.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$/);
        if (match) embed.searchParams.set('start', String(Number(match[1] || 0) * 3600 + Number(match[2] || 0) * 60 + Number(match[3] || 0)));
      }
      return embed.href;
    }
  }
  if (host === 'open.spotify.com') {
    const match = source.pathname.match(/^\/(?:embed\/|intl-[a-z-]+\/)?(episode|show|track|album|playlist|artist)\/([\w]+)(?:\/|$)/);
    if (match) return `https://open.spotify.com/embed/${match[1]}/${match[2]}`;
  }
  if (host === 'vimeo.com' || host === 'www.vimeo.com') {
    const match = source.pathname.match(/^\/(\d+)(?:\/([\w]+))?\/?$/);
    if (match) {
      const embed = new URL(`https://player.vimeo.com/video/${match[1]}`);
      const hash = match[2] || source.searchParams.get('h');
      if (hash) embed.searchParams.set('h', hash);
      return embed.href;
    }
  }
  if (/\.(docx?|pptx?|xlsx?)$/i.test(source.pathname)) {
    const embed = new URL('https://view.officeapps.live.com/op/embed.aspx');
    embed.searchParams.set('src', source.href);
    return embed.href;
  }
  return source.href;
}
