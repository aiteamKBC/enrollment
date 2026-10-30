import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from "@/hooks/useAuth.helpers";
import { inputClass } from '@/pages/users/components/ui';
import { getActivityContent, type Activity, type ActivityContent } from './api';
import { ProtectedFilePreview, QuizPreview, SourcePreview } from './ContentPreview';
import styles from './design.module.css';

const btnSecondary = styles.secondaryButton;

export function ActivityExpansion({ row, month, aptemId, initialDocumentId, onClose, loadContent, contentScope }: {
  row: Activity; month: string; aptemId?: number; initialDocumentId?: number; onClose: () => void;
  loadContent?: (rowId: number) => Promise<ActivityContent>; contentScope?: string;
}) {
  const { auth } = useAuth();
  const query = useQuery({ queryKey: ['old-otjh', auth.account?.id, 'activity-content', contentScope ?? aptemId ?? 'me', month, row.id],
    queryFn: () => loadContent ? loadContent(row.id) : getActivityContent(month, row.id, aptemId), staleTime: 60_000, refetchOnWindowFocus: false });
  const [selection, setSelection] = useState(initialDocumentId ? `doc-${initialDocumentId}` : '');
  const [quizSelection, setQuizSelection] = useState<number | null>(null);
  const [fileSelection, setFileSelection] = useState<number | null>(null);
  const parts = query.data?.parts ?? [];
  const documents = row.documents.filter(doc => doc.url);
  const options = [...parts.map(part => ({ id: `part-${part.id}`, title: part.title })), ...documents.map(doc => ({ id: `doc-${doc.id}`, title: doc.display_name }))];
  const selected = options.find(option => option.id === selection)?.id || options[0]?.id;
  const part = parts.find(item => `part-${item.id}` === selected);
  const doc = documents.find(item => `doc-${item.id}` === selected);
  return <section aria-label={`Content for ${row.title}`} className={styles.preview}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><h4 className="font-semibold text-foreground-900">{row.title}</h4><p className="mt-1 text-[12px] text-foreground-500">Activity content and documents</p></div>
      <button className={btnSecondary} onClick={onClose} aria-label={`Close ${row.title}`}>Close</button></div>
    {query.isPending && !documents.length && <p role="status" className="text-sm">Loading activity content…</p>}
    {query.error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm"><span>{query.error.message}</span><button className={btnSecondary} onClick={() => void query.refetch()}>Try again</button></div>}
    {options.length > 1 && <label className="block space-y-2 text-xs font-medium text-foreground-600">Reading, media or document
      <select className={inputClass} value={selected} onChange={event => setSelection(event.target.value)}>{options.map(option => <option value={option.id} key={option.id}>{option.title}</option>)}</select></label>}
    {part && <div key={part.id} className="space-y-4">
      {part.available === false && <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><strong>{part.issue === 'source_attempt_missing' ? 'Saved quiz attempt needs attention' : 'Original material needs attention'}</strong><p className="mt-1">{part.issue === 'source_attempt_missing' ? 'The original questions are available below. The learner’s saved answers still need to be restored before signing and completion.' : 'The source cannot currently provide this material. Signing and completion stay paused until it is restored.'}</p><button className={`${btnSecondary} mt-3`} onClick={() => void query.refetch()}>Retry material</button></div>}
      {(part.quiz || part.document) && (part.url || part.html || (part.quiz && part.document)) && <div className="flex flex-wrap gap-2" aria-label="Activity content view">
        {(part.url || part.html) && <button className={btnSecondary} aria-pressed={quizSelection !== part.id && fileSelection !== part.id} onClick={() => { setQuizSelection(null); setFileSelection(null); }}>Learning material</button>}
        {part.document && <button className={btnSecondary} aria-pressed={fileSelection === part.id} onClick={() => { setFileSelection(part.id); setQuizSelection(null); }}>Saved source file</button>}
        {part.quiz && <button className={btnSecondary} aria-pressed={quizSelection === part.id} onClick={() => { setQuizSelection(part.id); setFileSelection(null); }}>{part.quiz.attempt && part.quiz.answers_available !== false ? 'Saved quiz attempt' : 'Original quiz questions'}</button>}</div>}
      {part.quiz && (quizSelection === part.id || (!part.url && !part.html && !part.document)) ? <QuizPreview quiz={part.quiz} title={part.title} />
        : part.document && (fileSelection === part.id || (!part.url && !part.html)) && part.available !== false ? <ProtectedFilePreview file={part.document} />
          : (part.url || part.html) && part.available !== false ? <SourcePreview url={part.url} html={part.html} title={part.title} contentType={part.content_type} /> : null}
    </div>}
    {doc && <div key={doc.id} className="space-y-3"><a className="inline-flex text-[13px] font-medium text-primary-700 hover:underline" href={doc.url}>Download original</a><ProtectedFilePreview file={doc} /></div>}
    {!query.isPending && !query.error && !options.length && <p className="py-4 text-sm text-foreground-500">This activity has no linked content or documents. Its recorded details are shown in the report above.</p>}
  </section>;
}
