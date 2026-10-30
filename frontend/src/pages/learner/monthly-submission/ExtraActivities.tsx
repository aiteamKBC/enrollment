import { Link } from 'react-router-dom';
import { formatSystemTimestamp } from '@/lib/format';
import { useExtraActivities } from '@/api/extraActivities';
import { monthName } from './model';
import styles from './monthlySubmission.module.css';
const labels: Record<string, string> = { draft: 'Draft saved', submitted_for_tutor_review: 'Awaiting review', accepted: 'Accepted', rejected: 'Rejected', referred: 'Changes requested', partial: 'Partially accepted' };
export function ExtraActivities({ kind, learnerId, month }: { kind: string; learnerId: string; month?: string }) {
  const { activities, loading, error, refresh } = useExtraActivities(kind, learnerId, month);
  return <section aria-label="Extra activities" className="my-5 space-y-3">
    <div className={styles.sectionHeading}><h3>Extra activities</h3><button type="button" className={styles.secondary} onClick={refresh}>Refresh activities</button></div>
    {loading && <p role="status">Loading extra activities...</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && !activities.length && <p>No extra activities submitted{month ? ' this month' : ' yet'}.</p>}
    {activities.map(activity => <article key={activity.activityId} className={styles.submissionAttempt}>
      <div className={styles.sectionHeading}><h4>{activity.title || 'Untitled extra activity'}</h4><span className={styles.status} data-status={activity.status}>{labels[activity.status] || activity.status}</span></div>
      <p>{activity.hours || '0'} claimed hours{activity.ksbs.length ? ` | ${activity.ksbs.join(', ')}` : ''}</p>
      {activity.month && <p>Activity month: {monthName(activity.month)}</p>}
      {activity.submittedAt && activity.status !== 'draft' && <p>Submitted: {formatSystemTimestamp(activity.submittedAt, { dateStyle: 'medium', timeStyle: 'short' })} (UK time)</p>}
      <p className="whitespace-pre-wrap">{activity.reflection || activity.answer}</p>
      {activity.coachFeedback && <p className="whitespace-pre-wrap"><strong>Coach feedback: </strong>{activity.coachFeedback}</p>}
      <Link className={styles.secondary} to={`/learner/monthly-submission/${kind}/${learnerId}/extra-activities?activity=${encodeURIComponent(activity.activityId)}`}>{activity.status === 'draft' ? 'Continue draft' : activity.status === 'rejected' ? 'Revise extra activity' : 'View extra activity'}</Link>
    </article>)}
  </section>;
}
