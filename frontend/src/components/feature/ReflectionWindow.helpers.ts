


export function formatClock(totalSeconds: number): string {
  const safeSeconds = Number.isFinite(totalSeconds)
    ? Math.max(0, Math.floor(totalSeconds))
    : 0;
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;
  return [hours, minutes, seconds]
    .map((value) => String(value).padStart(2, '0'))
    .join(':');
}


/** Normalise stored completion clocks. Older records use MM:SS (and may have
 * more than 59 minutes); newer UI always displays HH:MM:SS. */
export function parseClockSeconds(value: string | null | undefined): number | null {
  const parts = String(value || '').trim().split(':');
  if (parts.length !== 2 && parts.length !== 3) return null;
  const units = parts.map(Number);
  if (units.some((part) => !Number.isFinite(part) || part < 0)) return null;
  if (units.at(-1)! >= 60 || (parts.length === 3 && units[1] >= 60)) return null;
  return parts.length === 3
    ? units[0] * 3600 + units[1] * 60 + units[2]
    : units[0] * 60 + units[1];
}


export function formatRecordedClock(value: string | null | undefined): string | null {
  const totalSeconds = parseClockSeconds(value);
  return totalSeconds == null ? null : formatClock(totalSeconds);
}
