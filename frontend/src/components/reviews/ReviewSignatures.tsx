import { useState } from 'react';
import { CheckCircle2, Clock3 } from 'lucide-react';
import type { ReviewParticipantRole, ReviewSignatureState } from '@/api/reviewInstances';

const roles: ReviewParticipantRole[] = ['advisor', 'participant', 'employer', 'referrer'];
const labels: Record<ReviewParticipantRole, string> = { advisor: 'Coach', participant: 'Learner', employer: 'Employer', referrer: 'Referrer' };

function signatureImage(value: string | null | undefined): string | null {
  const image = value?.trim() || '';
  // Saved signatures are raster data URLs. Never load an external resource or
  // render SVG/markup from a historical signature field.
  return /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/i.test(image) ? image : null;
}

function signedDate(value: string | null | undefined): string | null {
  if (!value) return null;
  // The API formats timestamps without an offset; those timestamps are UTC.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value.replace(' ', 'T')}Z`;
  const parsed = new Date(normalized);
  return Number.isFinite(parsed.getTime()) ? new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London',
  }).format(parsed) : null;
}

function SignatureCard({ role, state }: { role: ReviewParticipantRole; state: ReviewSignatureState }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const image = state.signed ? signatureImage(state.signature) : null;
  const date = state.signed ? signedDate(state.signedAt) : null;
  const label = labels[role];
  return <article aria-label={`${label} signature`} className="min-w-0 rounded-xl border border-background-200 bg-background-50 p-4">
    <div className="flex items-center justify-between gap-3">
      <h4 className="text-sm font-semibold text-foreground-900">{label}</h4>
      <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${state.signed ? 'text-emerald-700' : 'text-amber-700'}`}>
        {state.signed ? <CheckCircle2 size={15}/> : <Clock3 size={15}/>}{state.signed ? 'Signed' : 'Awaiting signature'}
      </span>
    </div>
    {state.signed ? <>
      {image && failedImage !== image
        ? <div className="my-3 flex min-h-24 items-center justify-center rounded-lg border border-slate-200 bg-white p-3">
          <img src={image} alt={`${label} signature`} className="max-h-24 max-w-full object-contain" onError={() => setFailedImage(image)}/>
        </div>
        : <p className="my-3 rounded-lg bg-background-100 p-3 text-sm text-foreground-500">A signature is recorded, but its image is unavailable.</p>}
      <p className="break-words text-sm font-semibold text-foreground-900">{state.signedName || 'Name not recorded'}</p>
      <p className="mt-1 text-xs text-foreground-500">{date ? `Signed ${date} (Europe/London)` : 'Signing date not recorded'}</p>
    </> : <p className="mt-3 text-sm text-foreground-500">{label === 'Learner' ? 'The learner' : `The ${label.toLowerCase()}`} still needs to sign this review.</p>}
  </article>;
}

/** The same saved evidence and pending parties on the coach and learner forms. */
export function ReviewSignatures({ signatures }: { signatures: Record<ReviewParticipantRole, ReviewSignatureState> }) {
  const required = roles.filter(role => signatures[role]?.required);
  if (!required.length) return null;
  const signed = required.filter(role => signatures[role].signed).length;
  return <section aria-label="Review signatures" className="rounded-2xl border border-background-200 bg-background-50 p-4 sm:p-5">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-base font-bold text-foreground-900">Signatures</h3>
      <p className="text-sm text-foreground-500">{signed} of {required.length} required signatures saved</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2">{required.map(role => <SignatureCard key={role} role={role} state={signatures[role]}/>)}</div>
    <p className="mt-4 text-sm text-foreground-600">{signed === required.length
      ? 'All required signatures are saved.'
      : 'The review is complete once all required signatures have been saved.'}</p>
  </section>;
}
