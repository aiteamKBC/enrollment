import type { TeamsLeftoverSlot } from '../module-builder/moduleAuthoringData';
import { calendarLabel } from "./createCalendarForm.helpers";

/** What one Teams slot outside the module plan is, in the author's words rather than Microsoft's. */
export function leftoverSlotLabel(slot: TeamsLeftoverSlot): string {
  if (slot.kind === 'series') {
    return `The whole ${slot.day || 'weekday'} Teams series`;
  }
  return slot.startDateTimeUtc ? calendarLabel(slot.startDateTimeUtc) : 'A Teams session with no readable date';
}
