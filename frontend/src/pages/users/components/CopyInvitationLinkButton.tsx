// ============================================================================
// Copy a learner's set-password link, for when the invitation email cannot
// reach them -- some employers' mail filters block our domain.
//
// Staff send the link themselves (Teams, WhatsApp, SMS). It is the ordinary
// single-use invitation link: it expires after 7 days and replaces any earlier
// one, so a previously emailed link stops working. It is shown only here and
// is not stored anywhere in the browser.
// ============================================================================
import { useState } from 'react';
import { apiInvitationLink } from '@/api/auth';
import { formatSystemTimestamp } from '@/lib/format';

export function CopyInvitationLinkButton({ subjectId, name, onIssued }: {
  subjectId: number;
  name: string;
  onIssued?: () => void;
}) {
  const [state, setState] = useState<'idle' | 'loading' | 'ready'>('idle');
  const [link, setLink] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function issue() {
    if (state === 'loading') return;
    setState('loading');
    setError(null);
    setCopied(false);
    try {
      const result = await apiInvitationLink(subjectId);
      setLink(result.link);
      setExpiresAt(result.expiresAt);
      setState('ready');
      // One click is enough: copy straight away. Some browsers refuse a copy
      // that follows a network call; the link then stays on screen with its
      // "Copy link" button, which copies on a fresh click.
      try {
        await navigator.clipboard.writeText(result.link);
        setCopied(true);
      } catch {
        setCopied(false);
      }
      onIssued?.();
    } catch (issueError) {
      setError(issueError instanceof Error ? issueError.message : 'Could not create the link.');
      setState('idle');
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setError('Copying is blocked in this browser. Select the link and copy it manually.');
    }
  }

  if (state === 'ready') {
    return (
      <span className="inline-flex max-w-[320px] flex-col items-start gap-1 rounded-lg border border-primary-200 bg-primary-50/60 p-2" role="group" aria-label={`Set-password link for ${name}`}>
        <span className="text-[11px] font-semibold text-primary-800" role="status">
          {copied ? `Link copied. Send it to ${name} on Teams, WhatsApp or SMS` : `Send this to ${name} on Teams, WhatsApp or SMS`}
        </span>
        <input
          readOnly
          value={link}
          aria-label={`Set-password link for ${name}`}
          onFocus={event => event.currentTarget.select()}
          className="w-full rounded-md border border-primary-200 bg-white px-2 py-1 font-mono text-[11px] text-foreground-700"
        />
        <span className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void copy()}
            className="inline-flex items-center gap-1 rounded-md bg-primary-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-primary-700"
          >
            <i className={copied ? 'ri-check-line' : 'ri-file-copy-line'} />
            {copied ? 'Copied' : 'Copy link'}
          </button>
          <span className="text-[11px] text-foreground-500">
            Works once{expiresAt ? `, until ${formatSystemTimestamp(expiresAt, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}` : ''}
          </span>
        </span>
        <span className="text-[10.5px] text-foreground-500">Any earlier invitation link or email for {name} no longer works.</span>
        {error && <span className="text-[11px] text-red-600" role="alert">{error}</span>}
      </span>
    );
  }

  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <button
        type="button"
        onClick={() => void issue()}
        disabled={state === 'loading'}
        title={`Get a set-password link for ${name} to send yourself, when our email cannot reach them`}
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-foreground-200 px-2.5 py-1 text-[12px] font-medium text-foreground-600 transition-smooth hover:border-primary-300 hover:bg-primary-50/60 hover:text-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <i className={`text-[13px] ${state === 'loading' ? 'ri-loader-4-line animate-spin' : 'ri-links-line'}`} />
        {state === 'loading' ? 'Creating…' : 'Copy set-password link'}
      </button>
      {error && <span className="max-w-[220px] text-[11px] text-red-600" role="alert">{error}</span>}
    </span>
  );
}
