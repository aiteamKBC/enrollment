import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import DOMPurify from 'dompurify';
import { SessionResults } from '@/components/feature/SessionResults';
import { previewComponent } from './learnerPreviewData';
import { componentContentKind } from '@/utils/learnerJourney';
import { parseVideoUrl } from '@/components/feature/VideoPlayer';
import { parseQuizPairAnswer } from '@/lib/quizPairAnswers';
import type { QuizQuestion, QuizAnswerValue } from '@/api/quizzes';
import type { ModuleCatalogueItem } from './moduleAuthoringData';

const ComponentBody = lazy(() => import('@/pages/learner/video-watch/page').then(module => ({ default: module.ComponentBody })));
const QuestionInput = lazy(() => import('@/pages/learner/quiz-take/page').then(module => ({ default: module.QuestionInput })));

type PreviewQuiz = { quiz: { id: number; title: string; packageType?: string }; questions: {
  id: number; text: string; questionType: QuizQuestion['type']; explanation?: string;
  answers: { id: number; text: string; isCorrect: boolean }[];
}[] };
function QuizPreview({ quizId }: { quizId: string }) {
  const [quiz, setQuiz] = useState<PreviewQuiz>();
  const [error, setError] = useState('');
  const [answers, setAnswers] = useState<Record<string, QuizAnswerValue>>({});
  useEffect(() => {
    const controller = new AbortController(); setQuiz(undefined); setError(''); setAnswers({});
    fetch(`/quiz_api/quizzes/${encodeURIComponent(quizId)}/preview/`, { credentials: 'include', signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error('Could not load quiz preview.'); return response.json() as Promise<PreviewQuiz>; })
      .then(value => { if (!controller.signal.aborted) setQuiz(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [quizId]);
  if (error) return <p role="alert">{error}</p>;
  if (!quiz) return <p role="status">Loading quiz preview…</p>;
  if (quiz.quiz.packageType === 'scorm') return <iframe title={`${quiz.quiz.title} preview`} src={`/quiz_api/quizzes/${quiz.quiz.id}/scorm/`} sandbox="allow-scripts" className="h-[65vh] w-full rounded-xl border" />;
  return <div className="space-y-4">{quiz.questions.map((question, index) => {
    const pairs = question.answers.map(answer => parseQuizPairAnswer(answer.text, question.questionType === 'image_matching' ? 'image_matching' : 'matching'));
    const model: QuizQuestion = { id: question.id, text: question.text, type: question.questionType, points: 1,
      sortOrder: index, explanation: null, answers: question.answers.map((answer, i) => ({ id: answer.id, text: answer.text,
        ...(question.questionType === 'matching' || question.questionType === 'image_matching' ? { left: pairs[i].left, leftKey: String(answer.id), imageUrl: pairs[i].imageUrl } : {}) })),
      rightOptions: pairs.map(pair => pair.right), answerCount: question.answers.length };
    return <article key={question.id} className="rounded-xl border bg-white p-5"><h3 className="mb-4 font-semibold">{index + 1}. {question.text}</h3>
      <QuestionInput question={model} value={answers[question.id]} onChange={value => setAnswers(current => ({ ...current, [question.id]: value }))} /></article>;
  })}</div>;
}

const noop = () => undefined;
export function LearnerPreview({ module, initialComponentId, onClose }: {
  module: ModuleCatalogueItem; initialComponentId?: string; onClose: () => void;
}) {
  const components = module.weekStructure.flatMap(week => week.components.map(component => ({ week, component })));
  const [selected, setSelected] = useState(initialComponentId || components[0]?.component.id || '');
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => previous?.focus();
  }, []);
  const current = components.find(item => item.component.id === selected);
  const component = current ? previewComponent(current.component) : undefined;
  const quizId = String(current?.component.settings.linkedQuizId || '');
  return <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-3 backdrop-blur-sm" onClick={onClose}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-label="Preview as learner" tabIndex={-1}
      onClick={event => event.stopPropagation()} onKeyDown={event => {
        if (event.key === 'Escape') onClose();
        if (event.key === 'Tab') {
          const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],input,textarea,select,[tabindex="0"]') || []);
          const first = controls[0], last = controls[controls.length - 1];
          if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }} className="flex h-[90vh] w-full max-w-7xl flex-col overflow-hidden rounded-2xl bg-background-50 shadow-2xl">
      <header className="flex items-center justify-between gap-4 border-b bg-white p-5"><div><p className="text-xs font-bold uppercase text-primary-600">Preview as learner</p><h2 className="mt-1 text-lg font-bold">{module.title}</h2><p className="mt-1 text-xs text-foreground-500">Preview only. Progress, attendance and assessment results are not saved.</p></div><button onClick={onClose} aria-label="Close learner preview" className="rounded-lg border px-3 py-2">Close</button></header>
      <div className="grid min-h-0 flex-1 md:grid-cols-[250px_minmax(0,1fr)]"><nav aria-label="Module content" className="overflow-auto border-r bg-white p-3">{module.weekStructure.map(week => <div key={week.id} className="mb-5"><h3 className="px-2 py-2 text-xs font-bold uppercase text-foreground-500">Week {week.weekNumber} · {week.title}</h3>{week.components.map(item => <button key={item.id} onClick={() => setSelected(item.id)} aria-current={selected === item.id ? 'true' : undefined} className={`mb-1 block w-full rounded-lg px-3 py-3 text-left text-sm ${selected === item.id ? 'bg-primary-50 font-semibold text-primary-700' : 'hover:bg-background-50'}`}>{item.title || 'Untitled component'}</button>)}</div>)}</nav>
        <main className="overflow-auto p-4 md:p-7"><Suspense fallback={<p role="status">Loading content preview…</p>}>{!component ? <p>No components have been added yet.</p> : <div key={selected} className="mx-auto max-w-4xl space-y-5"><h2 className="text-xl font-bold">{component.title}</h2>
          {(current?.component.type === 'quiz' || current?.component.type === 'monthly-ksb-quiz') && quizId ? <QuizPreview quizId={quizId} /> : <>
            {component.assignmentBrief && !component.assignmentBriefHtml && <p className="whitespace-pre-wrap rounded-xl border bg-white p-5">{component.assignmentBrief}</p>}
            {component.assignmentBriefHtml && <div className="rich-text-surface rounded-xl border bg-white p-5" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(component.assignmentBriefHtml) }} />}
            <ComponentBody component={component} contentKind={componentContentKind(component.type)} parsed={component.videoUrl ? parseVideoUrl(component.videoUrl) : null} title={component.title} preview onDuration={noop} onProgress={noop} onPlayingChange={noop} onEnded={noop} onUnsupported={noop} />
            {component.type === 'live_session' && (component.teamsLiveSessionId && component.teamsSessionNumber
              ? <SessionResults seriesId={component.teamsLiveSessionId} sessionNumber={component.teamsSessionNumber} preview />
              : <p className="rounded-xl border bg-amber-50 p-4 text-sm">Link this component to a Teams session to preview its saved recording and transcript.</p>)}
            {component.reflectionRequired && <label className="block rounded-xl border bg-white p-4 text-sm font-semibold">{component.reflectionQuestion || 'Your reflection'}<textarea className="mt-3 min-h-28 w-full rounded-lg border p-3 font-normal" placeholder="Try the learner reflection field…" /></label>}
          </>}</div>}</Suspense></main>
      </div>
    </div>
  </div>;
}
