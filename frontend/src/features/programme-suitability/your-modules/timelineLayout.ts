import { permittedOfferings, type AcademicModule, type Offering, type Result, type YourModulesData } from './model';

export const day = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / 86400000;
export const COHORT_LABEL_WIDTH = 180;
const MIN_LABEL_WIDTH = 210;

export interface ModuleOffering { module: AcademicModule; offering: Offering }
export interface CohortRow {
  id: string; name: string; start?: string; home: boolean; offerings: ModuleOffering[];
}

/** Group only the existing permitted offerings, without changing eligibility. */
export function cohortRows(data: YourModulesData, modules: AcademicModule[], results: Map<string, Result>): CohortRow[] {
  const rows = new Map<string, CohortRow>();
  // One verified indication opens eligible alternatives for every applicable
  // module. The table's individual Skills Radar results remain unchanged.
  const allowAlternatives = modules.some(module => results.get(module.id) === 'potential');
  for (const module of modules) for (const offering of permittedOfferings(data, module.id, allowAlternatives)) {
    const home = offering.cohortId === data.homeCohort?.id;
    const row = rows.get(offering.cohortId) ?? { id: offering.cohortId, name: offering.cohortName,
      start: home ? data.homeCohort?.start : offering.cohortStart, home, offerings: [] };
    row.offerings.push({ module, offering });
    rows.set(row.id, row);
  }
  for (const row of rows.values()) row.offerings.sort((a, b) =>
    a.offering.start.localeCompare(b.offering.start) || a.offering.end.localeCompare(b.offering.end) || a.offering.id.localeCompare(b.offering.id));
  // Older API responses may not contain cohortStart yet. Keep them after known
  // dates with a stable tie-break; never invent a date from a cohort label.
  return [...rows.values()].sort((a, b) => Number(b.home) - Number(a.home) ||
    (a.start ?? '9999-12-31').localeCompare(b.start ?? '9999-12-31') || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

export function timelineAxis(rows: CohortRow[], home: NonNullable<YourModulesData['homeCohort']>) {
  const dates = rows.flatMap(row => row.offerings.flatMap(({ offering }) => [offering.start, offering.end]));
  const first = [...dates, home.start].sort()[0];
  const last = [...dates, home.end].sort().at(-1)!;
  const from = new Date(`${first.slice(0, 7)}-01T00:00:00Z`);
  const until = new Date(`${last.slice(0, 7)}-01T00:00:00Z`);
  until.setUTCMonth(until.getUTCMonth() + 1);
  const left = from.getTime() / 86400000;
  const span = until.getTime() / 86400000 - left;
  const months: { label: string; left: number; width: number }[] = [];
  for (const cursor = new Date(from); cursor < until; cursor.setUTCMonth(cursor.getUTCMonth() + 1)) {
    const next = new Date(cursor); next.setUTCMonth(next.getUTCMonth() + 1);
    months.push({ label: cursor.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' }),
      left: (cursor.getTime() / 86400000 - left) / span * 100,
      width: (next.getTime() - cursor.getTime()) / 86400000 / span * 100 });
  }
  return { left, span, months, width: Math.max(900, months.length * 85) };
}

/** Pack date overlaps AND expanded short-offering labels into local lanes.
 * The date bar always retains its exact width; only the readable label may be
 * wider, or shift left near the axis end. Separate groups remain separate blocks.
 */
export function cohortLanes(row: CohortRow, axis: ReturnType<typeof timelineAxis>) {
  const lanes: { left: number; right: number }[][] = [];
  const blocks = row.offerings.map(item => {
    const start = (day(item.offering.start) - axis.left) / axis.span * axis.width;
    const width = (day(item.offering.end) - day(item.offering.start) + 1) / axis.span * axis.width;
    const labelWidth = Math.max(width, MIN_LABEL_WIDTH);
    const labelLeft = Math.min(start, axis.width - labelWidth);
    const footprint = { left: Math.min(start, labelLeft), right: Math.max(start + width, labelLeft + labelWidth) };
    let lane = lanes.findIndex(intervals => intervals.every(placed => footprint.left >= placed.right || footprint.right <= placed.left));
    if (lane < 0) { lane = lanes.length; lanes.push([]); }
    lanes[lane].push(footprint);
    return { ...item, lane, start, width, labelLeft, labelWidth, left: footprint.left, widthIncludingLabel: footprint.right - footprint.left };
  });
  return { blocks, count: lanes.length };
}
