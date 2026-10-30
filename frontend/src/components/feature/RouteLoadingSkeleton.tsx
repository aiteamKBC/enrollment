import { useLocation } from 'react-router-dom';
import { RecordPageSkeleton } from '@/features/old-otjh/RecordSkeletons';
import { PageSkeleton } from './Skeletons';

/** Preserve the destination's shell during both session and route loading. */
export function RouteLoadingSkeleton() {
  const { pathname } = useLocation();
  if (pathname === '/old-otjh/months' || pathname.startsWith('/old-otjh/months/')) {
    return <RecordPageSkeleton reportPage={pathname.replace(/\/$/, '') !== '/old-otjh/months'} />;
  }
  const learnerRoute = pathname === '/learner'
    || pathname.startsWith('/learner/')
    || pathname.startsWith('/workspace/learner');
  const coachRoute = pathname === '/coach'
    || pathname.startsWith('/coach/')
    || pathname === '/workspace/coach'
    || pathname.startsWith('/workspace/coach/');
  return <PageSkeleton workspaceRole={learnerRoute ? 'learner' : coachRoute ? 'coach' : undefined} hideSidebar={coachRoute} />;
}
