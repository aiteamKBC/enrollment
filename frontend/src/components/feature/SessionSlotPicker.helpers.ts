


/** A time as people read it — "9:00 AM" over the 24-hour value that is booked,
 *  so an afternoon slot can never be mistaken for a morning one. */
export function slotLabel(slot: string): string {
  const [hour, minute] = slot.split(':').map(Number);
  const suffix = hour < 12 ? 'AM' : 'PM';
  const display = hour % 12 === 0 ? 12 : hour % 12;
  return `${display}:${String(minute).padStart(2, '0')} ${suffix}`;
}
