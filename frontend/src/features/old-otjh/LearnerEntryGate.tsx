import { useEffect, useRef, type ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { Modal } from '@/pages/users/components/Modal';
import { fetchLearnerEntry } from './entry';

const available = (path: string) => ['/old-otjh', '/messages', '/learner/messages',
  '/learner/support', '/profile', '/learner/onboarding'].some(base => path === base || path.startsWith(`${base}/`));

export function LearnerEntryGate({ children }: { children: ReactNode }) {
  const { auth } = useAuth();
  if (auth.account?.role !== 'learner') return children;
  return <StudentEntry key={auth.account.id}>{children}</StudentEntry>;
}

function StudentEntry({ children }: { children: ReactNode }) {
  const { auth, logout } = useAuth();
  const { pathname } = useLocation();
  const exempt = available(pathname);
  const client = useQueryClient();
  const wasBlocked = useRef(false);
  const query = useQuery({ queryKey: ['learner-entry', auth.account?.id, pathname],
    // Keep the verification in flight through React StrictMode's immediate
    // unmount/remount. The account and pathname remain in the query key, so a
    // response cannot cross identities or routes; saved records still trigger
    // the explicit invalidation below.
    queryFn: () => fetchLearnerEntry(), retry: false, staleTime: Infinity,
    refetchOnMount: 'always', refetchOnWindowFocus: false });
  useEffect(() => { if (query.data?.required) wasBlocked.current = true; }, [query.data]);
  useEffect(() => {
    const refresh = () => { void client.invalidateQueries({ queryKey: ['learner-entry', auth.account?.id] }); };
    window.addEventListener('previous-record-updated', refresh);
    return () => window.removeEventListener('previous-record-updated', refresh);
  }, [client, auth.account?.id]);
  // A fresh check after signing in this or another tab returns to the new home.
  if (pathname.startsWith('/old-otjh') && wasBlocked.current && !query.isFetching
      && !query.error && query.data?.canAccess) return <Navigate to="/learner/home" replace />;
  if (exempt) return children;
  // Keep forms mounted during save-triggered revalidation, but suspend interaction.
  if (!query.error && query.data?.canAccess) return <div inert={query.isFetching} aria-busy={query.isFetching || undefined}>{children}</div>;
  if (query.isPending) return <div className="grid min-h-screen place-content-center gap-4 bg-[#f4f0e9] p-8 text-center">
    <p role="status">Checking your learning access…</p>
    <Link to="/messages" className="underline">Contact support</Link>
    <button type="button" onClick={logout} className="underline">Sign out</button>
  </div>;
  const checking = query.isPending || query.isFetching;
  return <div className="min-h-screen bg-[#f4f0e9]">
    <Modal title={query.error ? 'We could not check your learning access' : checking
      ? 'Checking your learning access' : 'Review and sign your previous learning record'}
      dismissible={false} onClose={() => undefined} size="max-w-xl"
      footer={<div className="flex flex-wrap items-center gap-3">
        <Link to="/messages" className="rounded-lg border px-4 py-2">Contact support</Link>
        <button type="button" className="rounded-lg border px-4 py-2" onClick={logout}>Sign out</button>
        {!checking && !query.error && <Link to="/old-otjh/months" className="rounded-lg bg-[#362044] px-5 py-3 font-semibold text-white">Review and sign</Link>}
      </div>}>
      {checking ? <p role="status">Checking your learner profile and saved signatures…</p>
        : query.error ? <div role="alert"><p>{query.error.message}</p>
          <button type="button" onClick={() => void query.refetch()} className="mt-4 rounded-lg bg-[#362044] px-5 py-3 text-white">Try again</button></div>
        : <div className="space-y-3"><p>Before entering your learning area, please review and sign the required months in your previous learning record through 31 August 2026.</p>
          <p>You can read each complete monthly record before adding your signature. Your access opens once your signature is saved and all required months are complete.</p>
          {query.data?.totalMonths ? <p>{query.data.completedMonths} of {query.data.totalMonths} months complete.</p>
            : <p>Your record needs checking. Contact support if no months are available to review.</p>}
        </div>}
    </Modal>
  </div>;
}
