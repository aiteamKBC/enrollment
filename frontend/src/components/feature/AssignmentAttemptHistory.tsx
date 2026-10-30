import type { SubmissionAttempt } from '@/api/assignmentAttempts';

const labels: Record<string, string> = {
  submitted_for_tutor_review: 'Awaiting coach review', pending: 'Awaiting coach review',
  accepted: 'Accepted', rejected: 'Rejected', referred: 'Referred back',
  partial: 'Partially awarded', escalated: 'Escalated',
};
const dateLabel = (value: string | null) => {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('en-GB', { timeZone: 'Europe/London' });
};

export function AssignmentAttemptHistory({ attempts }: { attempts: SubmissionAttempt[] }) {
  if (!attempts.length) return null;
  return <section aria-label="Assignment submission history" className="my-5 space-y-3 rounded-xl border border-slate-200 bg-white p-4">
    <h3 className="font-semibold text-slate-900">Submission history ({attempts.length} {attempts.length === 1 ? 'attempt' : 'attempts'})</h3>
    <p className="text-sm text-slate-600">Each submitted attempt keeps its own answer and review. Draft saves are not new submissions.</p>
    {attempts.map(attempt => <details key={attempt.number} className="rounded-lg border border-slate-200 p-3">
      <summary className="cursor-pointer text-sm font-semibold">Attempt {attempt.number} — {labels[attempt.status] || attempt.status}{attempt.submittedAt && <span className="ml-2 font-normal text-slate-600">{dateLabel(attempt.submittedAt)} (UK time)</span>}</summary>
      <div className="mt-3 space-y-3 text-sm">
        <div><h4 className="font-semibold">Submitted answer</h4><p className="whitespace-pre-wrap break-words">{attempt.answer || 'No written answer recorded.'}</p></div>
        <div><h4 className="font-semibold">Coach feedback</h4><p className="whitespace-pre-wrap break-words">{attempt.coachFeedback || 'No feedback recorded yet.'}</p></div>
        {(attempt.reviewedBy || attempt.reviewedAt) && <p className="text-slate-600">Reviewed by {attempt.reviewedBy || 'Coach'}{attempt.reviewedAt ? ` — ${dateLabel(attempt.reviewedAt)} (UK time)` : ''}</p>}
      </div>
    </details>)}
  </section>;
}
