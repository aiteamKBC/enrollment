import { audioAlternativeUrl } from '@/lib/audioAlternative.mjs';

export function AudioYouTubeAlternative({ url, onLeave }: { url: unknown; onLeave: () => void }) {
  const safeUrl = audioAlternativeUrl(url);
  if (!safeUrl) return null;
  return <div className="mt-3">
    <a href={safeUrl} target="_blank" rel="noopener noreferrer" onClick={onLeave}
      className="text-sm font-semibold text-violet-600 underline">Listen to this recording on YouTube</a>
    <p className="text-xs text-foreground-500 mt-1">Opens separately. Playback here pauses; return to the LMS to continue and save your activity.</p>
  </div>;
}
