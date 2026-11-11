import { newModuleId } from './moduleIdentity';
export interface AssignmentPeriod { year?: number; startMonth: number; startDay: number; endMonth: number; endDay: number }
export function cohortYear(name:string) {
  const years=Array.from(name.matchAll(/(?<!\d)(\d{4})(?!\d)/g));
  return years.length===1 ? Number(years[0][1]) : undefined;
}
export function validatePeriod(name: string, period: AssignmentPeriod) {
  const year=period.year ?? cohortYear(name);
  if (year===undefined || !Number.isInteger(year) || year<1000 || year>9999) throw new Error('Include a single year in the Cohort Name, for example Feb 2027.');
  const crossYear=period.startMonth>period.endMonth || (period.startMonth===period.endMonth && period.startDay>period.endDay);
  for (const [y,m,d] of [[year-Number(crossYear),period.startMonth,period.startDay],[year,period.endMonth,period.endDay]]) {
    const date=new Date(Date.UTC(y,m-1,d));
    if (![y,m,d].every(Number.isInteger) || date.getUTCFullYear()!==y || date.getUTCMonth()!==m-1 || date.getUTCDate()!==d) throw new Error('Enter valid assignment period month and day boundaries for this Cohort year.');
  }
  return {...period,year};
}
export const defaultPeriod = (startDate: string, legacy: Array<AssignmentPeriod & { active?: boolean }> = []): AssignmentPeriod | undefined => {
  const month = Number(startDate.slice(5, 7));
  const saved = legacy.filter(p => p.active !== false && p.endMonth === month);
  if (saved.length === 1) return { startMonth:saved[0].startMonth,startDay:saved[0].startDay,endMonth:saved[0].endMonth,endDay:saved[0].endDay };
  if (legacy.length) return undefined; // Never replace a customised historical calendar.
  return ({2:{startMonth:10,startDay:15,endMonth:2,endDay:15},6:{startMonth:2,startDay:16,endMonth:6,endDay:15},10:{startMonth:6,startDay:16,endMonth:10,endDay:14}} as Record<number,AssignmentPeriod>)[month];
};

/** Copy configuration only. KSB definitions and learner records never enter here. */
export function copyCohortConfiguration(source: { modules?: unknown[]; mappings?: unknown[]; routes?: unknown[]; selectionPolicy?:unknown; ownerAllocation?:unknown }) {
  const configuration = structuredClone({modules:source.modules || [],mappings:source.mappings || [],routes:source.routes || [],
    ...(source.selectionPolicy ? {selectionPolicy:source.selectionPolicy}:{}),
    ...(source.ownerAllocation ? {ownerAllocation:source.ownerAllocation}:{})});
  const ids = new Map<string,string>();
  const collect = (nodes: unknown[]) => nodes.forEach(value => {
    if (!value || typeof value !== 'object') return;
    const node=value as Record<string,unknown>;
    if (typeof node.id === 'string') ids.set(node.id,'');
    for (const key of ['items','modules','routes','weeklyTopics']) if (Array.isArray(node[key])) collect(node[key]);
  });
  collect(configuration.modules); collect(configuration.routes);
  const generated: Array<{id:string}>=[];
  for (const oldId of ids.keys()) {
    const id=newModuleId({source:configuration,generated});
    ids.set(oldId,id);generated.push({id});
  }
  const rewire = (value: unknown, field='', allocation=false): unknown => {
    const reference = field==='id' || /(?:module|pathway|route|owner).*(?:id|key)s?$/i.test(field);
    if (typeof value === 'string') return (reference || allocation) && !['title','description','summary'].includes(field) ? ids.get(value) || value : value;
    if (Array.isArray(value)) return value.map(item=>rewire(item,field,allocation));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key,item])=>[ids.get(key)||key,rewire(item,key,allocation || /ownerAllocation|allocationRules/i.test(key))]));
    return value;
  };
  return rewire(configuration) as typeof configuration;
}
