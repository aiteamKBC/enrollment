import type { ProgressStep } from './createRecovery';

export type UpdateProgressStage = 'calendar' | 'emails' | 'done';

export interface UpdateProgress {
  stage: UpdateProgressStage;
}

/**
 * The update request applies the calendar and invitation changes together.
 * The email stage starts once Microsoft has accepted that update and the
 * result dialog begins sending the LMS notifications.
 */
export function updateProgressSteps(
  progress: UpdateProgress | null,
  sessionCount: number,
): ProgressStep[] {
  const stage = progress?.stage || 'calendar';
  const sessions = `${sessionCount} session${sessionCount === 1 ? '' : 's'}`;
  return [
    {
      key: 'calendar',
      label: 'Teams calendar updated',
      detail: stage === 'calendar' ? `Sending ${sessions} to Microsoft` : `${sessions} accepted by Microsoft`,
      state: stage === 'calendar' ? 'active' : 'done',
    },
    {
      key: 'invitations',
      label: 'Invitations and meeting settings applied',
      detail: stage === 'calendar' ? 'Included in the calendar update' : 'Saved without recreating the meeting',
      state: stage === 'calendar' ? 'pending' : 'done',
    },
    {
      key: 'emails',
      label: 'LMS update emails sent',
      detail: stage === 'calendar' ? 'After Microsoft accepts the update' : stage === 'emails' ? 'Sending one email per person' : 'Notifications submitted',
      state: stage === 'calendar' ? 'pending' : stage === 'emails' ? 'active' : 'done',
    },
  ];
}
