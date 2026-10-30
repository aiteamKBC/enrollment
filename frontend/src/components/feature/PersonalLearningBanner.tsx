import { Link, useNavigate } from 'react-router-dom';
import { clearCachedResourcesContaining } from '@/api/cachedRequest';
import { clearPersonalLearning, type PersonalLearningContext } from '@/lib/personalLearning';

export function PersonalLearningBanner({ context }: { context: PersonalLearningContext }) {
  const navigate = useNavigate();
  const preview = context.mode !== 'study';
  return <aside aria-label="Learner mode" className="flex flex-wrap items-center justify-between gap-3 border-b border-primary-200 bg-primary-50 px-5 py-3 text-sm">
    <div><strong>{context.mode === 'study' ? 'Personal learning · Admin & Learner' : context.mode === 'all' ? 'Preview · All content' : 'Preview · Learner experience'}</strong>
      <p>{context.mode === 'study' ? 'Your progress is saved. Content only; outside official study reports.' : 'Your trial answers and results are not saved. Certificates are unavailable in preview.'}</p></div>
    {/* A preview opened from the builder has one way out, back to the builder
        kept open underneath it; leaving elsewhere would discard that editor. */}
    <div className="flex gap-2">{!preview && <Link className="rounded-lg border bg-white px-3 py-2" to="/my-courses">My Courses</Link>}
      <button type="button" className="rounded-lg bg-primary-600 px-3 py-2 font-semibold text-white" onClick={() => {
        // Every read of this course carried its identity, so dropping those
        // entries is enough; admin pages keep their own caches.
        clearPersonalLearning(); clearCachedResourcesContaining(context.id); navigate(context.returnTo);
      }}>{preview && context.returnTo.startsWith('/curriculum/module-builder') ? 'Return to Module Builder' : 'Return to admin'}</button></div>
  </aside>;
}
