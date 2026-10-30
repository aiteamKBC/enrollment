import { Link } from 'react-router-dom';
import type { PreviousLearningCompletion } from '@/api/learnerDetail';
import { learningHref } from './subjectLearning';

export function PreviousLearningNotice({ learning, kind, learnerId }: {
  learning?: PreviousLearningCompletion; kind?: string; learnerId?: string;
}) {
  if (!learning?.completed) return null;
  const href = `${learningHref('catalogue', kind, learnerId, learning.courseRef)}&activity=${encodeURIComponent(learning.activityId)}`;
  return <p className="my-3 rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800">
    Completed in previous learning.{' '}
    {kind && learnerId && <Link className="font-semibold underline" to={href}>View original record</Link>}
  </p>;
}
