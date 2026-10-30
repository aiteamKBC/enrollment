// ============================================================================
// Where an activity opens.
//
// Videos and quizzes keep their own pages; everything else goes through the
// component runner. Shared, because the sidebar that links to these appears on
// both the component page and the quiz page.
// ============================================================================
import type { JourneyComponent } from '@/utils/learnerJourney';

export function componentRoute(
  kind: string | undefined,
  id: string | undefined,
  c: JourneyComponent,
  module: string,
  week: string,
): string {
  const scope = `?module=${encodeURIComponent(module)}&week=${encodeURIComponent(week)}`;
  if (c.isQuiz && c.quizMeta?.quizId != null) {
    return `/learner/quiz/${kind}/${id}/${c.quizMeta.quizId}${scope}`;
  }
  const base = (c.type || '').toLowerCase() === 'video' ? 'video' : 'component';
  return `/learner/${base}/${kind}/${id}/${c.componentId}${scope}`;
}
