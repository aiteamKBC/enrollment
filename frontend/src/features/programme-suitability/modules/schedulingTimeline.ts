/** Day-based layout and isolated synthetic fixtures; no database access. */
export interface SampleOffering {
  id: string; moduleId: string; delivery: string; group: string; start: string; end: string;
}
const dayMs = 86_400_000;
export const dateDay = (date: string) => Date.parse(`${date}T00:00:00Z`) / dayMs;
export function londonDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const part = (type: string) => parts.find(p => p.type === type)!.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}
const iso = (year: number, month: number, day: number) => new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
export function sampleOfferings(moduleIds: string[], today: string): SampleOffering[] {
  const year = Number(today.slice(0, 4)) + 1;
  return moduleIds.flatMap((moduleId, index) => (index === moduleIds.length - 1 ? [0] : [0, 1]).map(group => ({
    id: `sample:${moduleId}:G${group + 1}`, moduleId, delivery: group === 0 ? 'A' as const : 'B' as const,
    group: `G${group + 1}`, start: iso(year, 1 + index * 2 + group * 5, 10 + group * 3),
    end: iso(year, 4 + index * 2 + group * 5, 18 + group * 3),
  })));
}
export function futureOfferings(offerings: SampleOffering[], today: string): SampleOffering[] {
  return offerings.filter(o => /^\d{4}-\d{2}-\d{2}$/.test(o.start) && /^\d{4}-\d{2}-\d{2}$/.test(o.end)
    && Number.isFinite(dateDay(o.start)) && Number.isFinite(dateDay(o.end)) && o.start > today && o.end >= o.start)
    .sort((a, b) => a.start.localeCompare(b.start) || a.id.localeCompare(b.id));
}
export function timelineMonths(offerings: SampleOffering[], today: string) {
  const earliest = offerings.reduce((first, o) => !first || o.start < first ? o.start : first, '');
  const anchor = new Date(`${earliest || today}T00:00:00Z`);
  const start = iso(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1);
  const fallback = iso(anchor.getUTCFullYear(), anchor.getUTCMonth() + 11, 28);
  const last = offerings.reduce((end, o) => o.end > end ? o.end : end, offerings.length ? offerings[0].end : fallback);
  const endDate = new Date(`${last}T00:00:00Z`);
  const end = iso(endDate.getUTCFullYear(), endDate.getUTCMonth() + 2, 1);
  const totalDays = dateDay(end) - dateDay(start);
  const months: { start: string; end: string; year: number; name: string; left: number; width: number }[] = [];
  for (let date = new Date(`${start}T00:00:00Z`); date.toISOString().slice(0, 10) < end; date.setUTCMonth(date.getUTCMonth() + 1)) {
    const first = date.toISOString().slice(0, 10);
    const next = iso(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
    months.push({ start: first, end: next, year: date.getUTCFullYear(), name: date.toLocaleString('en-GB', { month: 'short', timeZone: 'UTC' }),
      left: (dateDay(first) - dateDay(start)) / totalDays * 100, width: (dateDay(next) - dateDay(first)) / totalDays * 100 });
  }
  return { start, end, totalDays, months };
}
export function offeringPosition(offering: SampleOffering, start: string, totalDays: number) {
  return { left: `${(dateDay(offering.start) - dateDay(start)) / totalDays * 100}%`,
    width: `${(dateDay(offering.end) - dateDay(offering.start) + 1) / totalDays * 100}%` };
}
