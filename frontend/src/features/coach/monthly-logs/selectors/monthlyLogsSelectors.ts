import type { CoachMonthlyLogLearner } from '../types/monthlyLogs.types';

export function filterCoachMonthlyLogLearners(learners: CoachMonthlyLogLearner[], search: string) {
  const query = search.toLowerCase();
  return learners.filter(learner => `${learner.name} ${learner.programme}`.toLowerCase().includes(query));
}
