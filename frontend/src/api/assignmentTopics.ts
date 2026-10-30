import { learningFetch } from '@/lib/personalLearning';
import { invalidateLearnerReads } from './learnerRead';

export interface AssignmentTopicState { topicId: string; status: string; elapsedSeconds: number; month?: string; meetingKey?: string; submittedAt?: string }
export interface AssignmentTopicIdentity { learnerKind: 'commercial' | 'apprenticeship'; learnerId: string; activityId: string }

export async function loadAssignmentTopicStates(input: AssignmentTopicIdentity): Promise<AssignmentTopicState[]> {
  const query = new URLSearchParams({ ...input, activityType: 'assignment', view: 'topics' });
  const response = await learningFetch(`/learner_api/reflection/submissions/?${query}`);
  const data = await response.json();
  if (!response.ok || !Array.isArray(data.topics)) throw new Error(data.error || 'Could not load assignment topics.');
  return data.topics;
}

export async function selectAssignmentTopic(input: AssignmentTopicIdentity & {
  assignmentTopicId: string; assignmentElapsedSeconds: number; month: string;
  activityTitle: string; plannedOtjh: string; learnerName: string; programmeName: string; moduleTitle: string; weekTitle: string;
}): Promise<void> {
  const { month, ...fields } = input;
  const response = await learningFetch('/learner_api/reflection/submissions/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...fields, activityType: 'assignment', submissionMode: 'draft', selectAssignmentTopic: true,
      monthlyAssignment: { version: 2, month, step: 0, timeEntries: [] }, learningReflection: '' }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not save the selected topic. Please retry.');
  invalidateLearnerReads();
}
