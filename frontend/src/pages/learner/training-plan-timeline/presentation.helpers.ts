import type { TimelineModule } from './model';


export const hours = (value: number | null | undefined) => value == null ? '—' : `${Number(value.toFixed(2))}`;

export const dateLabel = (value: string) => value ? new Date(`${value.slice(0, 10)}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(Number(value.slice(0, 4)) !== new Date().getFullYear() ? { year: 'numeric' as const } : {}) }) : 'Dates coming soon';

export const monthLabel = (value: string) => new Date(`${value}-01T12:00:00Z`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });

export const sessionTime = (value: string) => new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' });

export function moduleStatus(module: Pick<TimelineModule, 'activityCount' | 'done'>) { return module.activityCount && module.done === module.activityCount ? 'Completed' : module.done ? 'In progress' : 'Not started'; }
