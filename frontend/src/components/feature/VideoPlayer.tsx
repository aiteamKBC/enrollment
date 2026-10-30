import { useEffect, useRef, useState } from 'react';

/* ═══════════════════════════════════════════════════════
   VIDEO PLAYER — renders the right player for a URL and
   reports real playback progress (duration, currentTime,
   ended) upward. Supports:
     - YouTube  (via the IFrame Player API — real duration/time/end)
     - Vimeo    (plain iframe; no progress events -> onUnsupported)
     - direct file .mp4/.webm/... (HTML5 <video> — native events)
   ═══════════════════════════════════════════════════════ */

export type PlaybackKind = 'youtube' | 'vimeo' | 'file';

export interface ParsedVideo {
  kind: PlaybackKind;
  youTubeId?: string;
  src: string;                 // embed src (vimeo) or file url
  fallbackSrc?: string;        // provider preview when viewing is allowed but downloading is not
}

/**
 * Authors paste a provider's "copy embed code" snippet — a whole `<iframe …>`
 * tag — into the Embed field as often as they paste a bare address. Only the
 * src is playable: handing the markup itself to an iframe made the browser
 * resolve it as a relative path, which landed the learner on a 404 instead of
 * the video. The learner API already unwraps this server-side; do the same for
 * anything parsed in the browser (the module builder's learner preview builds
 * its components client-side and never passes through that API).
 */
// Kept here because the parser and player share the provider contract.
// eslint-disable-next-line react-refresh/only-export-components
export function embeddedSrc(value: string): string {
  if (!value.includes('<')) return value;
  const src = value.match(/<iframe\b[^>]*?\ssrc\s*=\s*["']([^"']+)["']/i)?.[1];
  // Entity-decode: an embed snippet carries query separators as &amp;.
  return src ? src.replace(/&amp;/g, '&').trim() : value;
}

/**
 * Whether a source will be refused by the browser when we frame it.
 *
 * SharePoint and Stream answer with `frame-ancestors 'self' teams.microsoft.com
 * … *.cloud.microsoft` — Microsoft's own surfaces only. The LMS origin is not on
 * that list, so the frame is blocked and the learner gets "refused to connect"
 * where the video belongs. Nothing in our code can override that: the header
 * comes from the responding server, and the browser enforces it against us.
 * Reported at authoring time so it does not reach a learner.
 */
// Kept here because the parser and player share the provider contract.
// eslint-disable-next-line react-refresh/only-export-components
export function framingRefusedHost(url: string): string {
  let host = '';
  try {
    host = new URL(embeddedSrc(url.trim()).trim()).hostname.toLowerCase();
  } catch {
    return ''; // Not an absolute address — nothing to judge yet.
  }
  // The tenant host and Stream's own player both carry the SharePoint policy.
  const refused = /(^|\.)sharepoint\.com$|(^|\.)sharepoint-df\.com$|^web\.microsoftstream\.com$/.test(host);
  return refused ? host : '';
}

/** Classify a provider URL and extract the id/src needed to play it. */
// Kept here because the parser and player share the provider contract.
// eslint-disable-next-line react-refresh/only-export-components
export function parseVideoUrl(url: string): ParsedVideo {
  const clean = embeddedSrc(url.trim()).trim();
  const yt =
    clean.match(/(?:youtu\.be\/)([\w-]{6,})/) ||
    clean.match(/[?&]v=([\w-]{6,})/) ||
    clean.match(/youtube\.com\/embed\/([\w-]{6,})/);
  if (yt) return { kind: 'youtube', youTubeId: yt[1], src: `https://www.youtube.com/embed/${yt[1]}` };
  const vm = clean.match(/vimeo\.com\/(?:video\/)?(\d+)/);
  if (vm) return { kind: 'vimeo', src: `https://player.vimeo.com/video/${vm[1]}` };
  if (/\.(mp4|webm|ogg|mov|m4v)(\?.*)?$/i.test(clean)) return { kind: 'file', src: clean };
  // Google Drive. Use our same-origin media proxy rather than Drive's /preview
  // iframe: Drive can show "Unable to load video" for playable files, while the
  // proxy returns the actual MP4 and preserves Range requests for seeking.
  const drive =
    clean.match(/drive\.google\.com\/file\/d\/([\w-]{10,})/) ||
    clean.match(/drive\.google\.com\/open\?id=([\w-]{10,})/) ||
    clean.match(/drive\.google\.com\/uc\?[^#]*id=([\w-]{10,})/);
  if (drive) {
    const preview = new URL(`https://drive.google.com/file/d/${drive[1]}/preview`);
    const resourceKey = new URL(clean, 'https://drive.google.com').searchParams.get('resourcekey');
    if (resourceKey) preview.searchParams.set('resourcekey', resourceKey);
    return { kind: 'file', src: `/learner_api/media/google-drive/${drive[1]}/`, fallbackSrc: preview.href };
  }
  // Unknown — best-effort iframe, treated like vimeo (no progress events).
  return { kind: 'vimeo', src: clean };
}

interface YTNamespace {
  Player: new (el: HTMLElement | string, opts: unknown) => {
    getDuration: () => number;
    getCurrentTime: () => number;
    isMuted: () => boolean;
    getVolume: () => number;
    destroy: () => void;
  };
  PlayerState: { ENDED: number; PLAYING: number; PAUSED: number };
}
declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

/** Load the YouTube IFrame API once; resolve when window.YT is ready. */
let ytApiPromise: Promise<YTNamespace> | null = null;
function loadYouTubeApi(): Promise<YTNamespace> {
  if (window.YT && window.YT.Player) return Promise.resolve(window.YT);
  if (ytApiPromise) return ytApiPromise;
  ytApiPromise = new Promise<YTNamespace>((resolve) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prev?.();
      if (window.YT) resolve(window.YT);
    };
    const tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    document.head.appendChild(tag);
  });
  return ytApiPromise;
}

export function VideoPlayer({
  parsed, title, onDuration, onProgress, onPlayingChange, onEnded, onUnsupported,
}: {
  parsed: ParsedVideo;
  title: string;
  onDuration?: (seconds: number) => void;   // real total length, once known
  onProgress?: (currentSeconds: number) => void; // real elapsed within the video
  onPlayingChange?: (playing: boolean) => void; // audible playback eligible for timing; muted/seeking time is excluded
  onEnded?: () => void;                      // playback reached the end
  onUnsupported?: () => void;                // no progress events available (Vimeo/unknown)
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const nativePlayingRef = useRef(false);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);
  const useProviderPreview = parsed.kind === 'file' && failedSource === parsed.src && !!parsed.fallbackSrc;
  const cbRef = useRef({ onDuration, onProgress, onPlayingChange, onEnded, onUnsupported });
  cbRef.current = { onDuration, onProgress, onPlayingChange, onEnded, onUnsupported };

  // A volume change must not start timing a paused, buffering or unloaded video.
  const reportNativePlayback = (video: HTMLVideoElement, playing = nativePlayingRef.current) => {
    nativePlayingRef.current = playing;
    cbRef.current.onPlayingChange?.(playing && !video.muted && video.volume > 0);
  };
  useEffect(() => {
    if (parsed.kind !== 'file') return;
    return () => {
      nativePlayingRef.current = false;
      cbRef.current.onPlayingChange?.(false);
    };
  }, [parsed.kind, parsed.src]);

  // ── YouTube: IFrame Player API ──
  useEffect(() => {
    if (parsed.kind !== 'youtube' || !parsed.youTubeId || !mountRef.current) return;
    let player: InstanceType<YTNamespace['Player']> | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    let playing = false;
    const reportPlayback = () => {
      cbRef.current.onPlayingChange?.(playing && player?.isMuted?.() === false && (player?.getVolume?.() ?? 0) > 0);
    };

    loadYouTubeApi().then((YT) => {
      if (cancelled || !mountRef.current) return;
      const host = document.createElement('div');
      mountRef.current.appendChild(host);
      player = new YT.Player(host, {
        videoId: parsed.youTubeId,
        width: '100%',
        height: '100%',
        playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
        events: {
          onReady: () => {
            const d = player?.getDuration() ?? 0;
            if (d > 0) cbRef.current.onDuration?.(Math.round(d));
          },
          onStateChange: (e: { data: number }) => {
            // Duration is reliable only once playback starts.
            if (e.data === YT.PlayerState.PLAYING) {
              const d = player?.getDuration() ?? 0;
              if (d > 0) cbRef.current.onDuration?.(Math.round(d));
            }
            playing = e.data === YT.PlayerState.PLAYING;
            reportPlayback();
            if (e.data === YT.PlayerState.ENDED) cbRef.current.onEnded?.();
          },
        },
      });
      // YouTube exposes mute/volume through getters, without a volume event.
      // Recheck on the progress poll and before/after a tab switch.
      document.addEventListener('visibilitychange', reportPlayback);
      // Poll current time for a smooth countdown (state events alone are too coarse).
      poll = setInterval(() => {
        reportPlayback();
        const t = player?.getCurrentTime?.();
        if (typeof t === 'number') cbRef.current.onProgress?.(t);
      }, 500);
    });

    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', reportPlayback);
      cbRef.current.onPlayingChange?.(false);
      if (poll) clearInterval(poll);
      try { player?.destroy(); } catch { /* player may not be constructed yet */ }
    };
  }, [parsed.kind, parsed.youTubeId]);

  // ── Vimeo / unknown: plain iframe, no progress events ──
  useEffect(() => {
    if (parsed.kind === 'vimeo' || useProviderPreview) cbRef.current.onUnsupported?.();
  }, [parsed.kind, useProviderPreview]);

  if (parsed.kind === 'youtube') {
    return <div ref={mountRef} className="absolute inset-0 w-full h-full [&>div]:w-full [&>div]:h-full" />;
  }

  if (parsed.kind === 'file' && !useProviderPreview) {
    return (
      <video
        key={`${parsed.src}:${retryVersion}`}
        src={parsed.src}
        controls
        preload="metadata"
        className="absolute inset-0 w-full h-full bg-black"
        onLoadedMetadata={(e) => onDuration?.(Math.round((e.target as HTMLVideoElement).duration))}
        onTimeUpdate={(e) => onProgress?.((e.target as HTMLVideoElement).currentTime)}
        onPlaying={(e) => reportNativePlayback(e.currentTarget, true)}
        onVolumeChange={(e) => reportNativePlayback(e.currentTarget)}
        onLoadStart={(e) => reportNativePlayback(e.currentTarget, false)}
        onWaiting={(e) => reportNativePlayback(e.currentTarget, false)}
        onSeeking={(e) => reportNativePlayback(e.currentTarget, false)}
        onEmptied={(e) => reportNativePlayback(e.currentTarget, false)}
        onPause={(e) => reportNativePlayback(e.currentTarget, false)}
        onEnded={(e) => { reportNativePlayback(e.currentTarget, false); onEnded?.(); }}
        onError={(e) => { reportNativePlayback(e.currentTarget, false); setFailedSource(parsed.src); }}
      >
        Your browser does not support the video tag.
      </video>
    );
  }

  if (useProviderPreview) {
    return (
      <div className="absolute inset-0 flex flex-col bg-black">
        <div className="flex flex-wrap items-center justify-between gap-2 bg-slate-900 px-4 py-3 text-sm text-white">
          <p role="status">Video streaming could not load. Trying the provider preview.</p>
          <button
            type="button"
            className="rounded-md border border-white/50 px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
            onClick={() => { setFailedSource(null); setRetryVersion((version) => version + 1); }}
          >
            Retry video
          </button>
        </div>
        <iframe
          src={parsed.fallbackSrc}
          title={title}
          className="min-h-0 w-full flex-1"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
        />
      </div>
    );
  }

  // Vimeo / unknown — plain embed (progress tracked via onUnsupported fallback).
  return (
    <iframe
      src={parsed.src}
      title={title}
      className="absolute inset-0 w-full h-full"
      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
      allowFullScreen
    />
  );
}
