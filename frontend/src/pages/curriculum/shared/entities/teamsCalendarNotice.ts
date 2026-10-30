// ============================================================================
// "The Teams calendar has not moved with these dates."
//
// Changing a module's dates, or a group's delivery days, regenerates the plan
// this system holds. It does not touch the Teams series that plan was sent to:
// that calendar lives in the organizer's mailbox, and nothing about a date
// change reaches it on its own. Without something said at the moment of the
// edit the two quietly disagree -- the module shows the new days while everyone
// invited still holds the old invitations -- and the difference is only found by
// opening the module's Teams tab and reading "On the plan's dates: No".
//
// So the saves that move dates name the affected calendars back, and this is the
// one thing every screen that makes such a change says about them. When a single
// module is affected the new dates can be sent from the dialog itself: the walk
// to the Teams Meetings page to press one button was the step the change used to
// be left undone in. Several at once still go to that page, because pushing a
// run of series means a round of update and cancellation mail per series and
// that should be set off against a list, not a count.
// ============================================================================

import { showCurriculumAlert, showCurriculumConfirm } from '@/components/feature/CurriculumSweetAlert';
import type { StaleTeamsCalendar } from '@/lib/curriculumApi';
import { pushModulePlanToTeams, type TeamsCalendarPushResult } from './teamsCalendarPush';
import { finishTeamsUpdate } from '../../teams-meetings/creationResult';
import { isTeamsReviewCancelled } from '../../teams-meetings/calendarReview';

/** The Teams Meetings page, opened on the module whose calendar has to move. */
export function teamsMeetingsPath(calendar?: StaleTeamsCalendar | null): string {
  const moduleId = String(calendar?.moduleCatalogueId || '').trim();
  return moduleId
    ? `/curriculum/teams-meetings?module=${encodeURIComponent(moduleId)}`
    : '/curriculum/teams-meetings?state=out-of-sync';
}

/**
 * Raise the notice, and take the reader to the calendar if they say yes.
 *
 * Returns false when there was nothing to warn about, which is the common case:
 * most modules have no Teams calendar at all. A caller shows its own "saved"
 * confirmation then. When it returns true the reader has already been told the
 * save landed -- as the opening clause of the warning -- so a second dialog on
 * top of it would only say the same thing twice, and would say the reassuring
 * half last.
 *
 * `navigate` is passed in rather than imported: these run inside form drawers
 * that already hold the router, and a hook cannot be called from a save handler.
 */
export async function confirmTeamsCalendarUpdate({
  calendars,
  savedText,
  navigate,
  sessionTimes,
  weeks,
}: {
  calendars: StaleTeamsCalendar[] | undefined;
  /** What the save landed, in one clause -- e.g. "Group A is saved." */
  savedText: string;
  navigate: (path: string) => void;
  /**
   * The hour the group meets, so a push keeps the series on its own time while
   * moving which days it runs. Omitted, the meeting's stored duration and 09:00
   * stand in.
   */
  sessionTimes?: { startTime?: string; endTime?: string };
  /** The authored week count, when the caller is the module's own form. */
  weeks?: number;
}): Promise<boolean> {
  const affected = (calendars || []).filter(item => String(item?.moduleCatalogueId || '').trim());
  if (!affected.length) return false;
  const name = String(affected[0]?.moduleName || '').trim();
  // One module is named; several are counted. A list of eight module titles in a
  // dialog is not read, and the page they are being sent to lists them anyway.
  const subject = affected.length === 1
    ? (name ? `${name}'s Teams calendar is` : 'Its Teams calendar is')
    : `${affected.length} Teams calendars under it are`;
  // One calendar is sent from here, on the spot: the trip to the Teams Meetings
  // page to press one button was the step this change was left undone in. Many
  // calendars still go to that page -- pushing eight series from a dialog is
  // eight rounds of update mail to everyone invited, which nobody should set off
  // without seeing the list first.
  const pushHere = affected.length === 1;
  // Open the full review after this notice closes; SweetAlert has one dialog.
  let pushed: TeamsCalendarPushResult | null = null;
  const confirmed = await showCurriculumConfirm({
    title: 'Teams calendar still on the old dates',
    text: `${savedText} ${subject} still on the dates from before this change. Nothing reaches Teams until the calendar is sent the new ones, so everyone invited is holding the old invitations until then.`,
    icon: 'warning',
    confirmButtonText: pushHere ? 'Send the new dates to Teams' : 'Open Teams Meetings',
    cancelButtonText: 'Not now',
    onConfirm: async () => {
      if (!pushHere) {
        navigate(teamsMeetingsPath(null));
        return;
      }
      // The full review opens after this notice closes, so two dialogs do not
      // replace each other while a save is pending.
    },
  });
  if (confirmed && pushHere) {
    try {
      pushed = await pushModulePlanToTeams({
        moduleCatalogueId: affected[0].moduleCatalogueId,
        moduleName: name,
        weeks,
        startTime: sessionTimes?.startTime,
        endTime: sessionTimes?.endTime,
      });
    } catch (error) {
      if (!isTeamsReviewCancelled(error)) await showCurriculumAlert({
        title: 'Teams calendar was not confirmed', icon: 'error',
        text: error instanceof Error ? error.message : 'Review the calendar before retrying.',
      });
    }
  }
  if (pushed) {
    const { sessionCount, warning, outcome, sent } = pushed as TeamsCalendarPushResult;
    if (outcome.notifyAttendees || outcome.addedPeople?.length) {
      // The author ticked the change email in the review: the result stays
      // open to send it and report it, instead of a notice that times out.
      await finishTeamsUpdate(outcome, sent);
      return true;
    }
    await showCurriculumAlert({
      title: warning ? 'Sent with warnings' : 'Teams calendar updated',
      text: warning
        ? `${sessionCount} date${sessionCount === 1 ? '' : 's'} were sent, but Microsoft Teams did not accept every one. ${warning}`
        : `${sessionCount} session date${sessionCount === 1 ? '' : 's'} sent to the Teams calendar. Everyone invited has the new invitations.`,
      timer: warning ? undefined : 2200,
    });
  }
  return true;
}
