import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { useResolvedLearner } from '@/hooks/useMyLearner';
import { AppIcon } from '@/components/feature/AppIcon';
import { getWorkspaceRecordLink } from './api';

export function PreviousRecordMenuItem({ onNavigate }: { onNavigate: () => void }) {
  const { auth } = useAuth();
  if (auth.account?.role === 'learner') return <RecordLink onNavigate={onNavigate} />;
  if (auth.account?.role === 'admin' || auth.account?.role === 'staff') return <SelectedRecordLink onNavigate={onNavigate} />;
  return null;
}

function SelectedRecordLink({ onNavigate }: { onNavigate: () => void }) {
  const { kind: urlKind, id: urlId } = useParams<{ kind: string; id: string }>();
  const { id } = useResolvedLearner(urlKind, urlId);
  return id ? <RecordLink learnerId={id} onNavigate={onNavigate} /> : null;
}

function RecordLink({ learnerId, onNavigate }: { learnerId?: string; onNavigate: () => void }) {
  const { auth } = useAuth();
  const query = useQuery({ queryKey: ['old-otjh', auth.account?.id, 'workspace-link', learnerId ?? 'me'],
    queryFn: () => getWorkspaceRecordLink(learnerId), retry: false, staleTime: 0, refetchOnMount: 'always' });
  const href = query.data?.href;
  // Hide while the server verifies eligibility; never use the staff directory
  // as a fallback for new learners, missing links, or failed requests.
  const ownMonths = learnerId === undefined ? href === '/old-otjh/months'
    : typeof href === 'string' && /^\/old-otjh\/coach\/[1-9]\d*\/months\?workspace=learner$/.test(href);
  if (query.isFetching || query.error || !ownMonths || !href) return null;
  return <Link role="menuitem" to={href} onClick={onNavigate}
    className="group flex w-full cursor-pointer items-center gap-3 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-primary-50 hover:text-primary-700 focus-visible:bg-primary-50 focus-visible:text-primary-700">
    <AppIcon className="ri-history-line text-lg" />
    <span className="text-[0.8125rem] font-semibold">Previous learning record</span>
  </Link>;
}
