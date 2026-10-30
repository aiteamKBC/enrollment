import type { Activity, MonthState, Summary } from './api';

export function contentUrl(value?: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export const monthLabel = (month: string) => new Date(`${month}-01T12:00:00`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
export function hours(value: number | string | null | undefined) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export function duration(value: number | string | null | undefined) {
  if (value == null || value === '' || !Number.isFinite(Number(value)) || Number(value) < 0) return '—';
  const minutes = Math.round(Number(value) * 60);
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}
export function displayDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}
export function monthStatus(month: MonthState) {
  if (month.status === 'complete') return 'Complete';
  if (month.student_signature) return 'Ready to complete';
  return 'Awaiting signature';
}
export function nextOutstanding(summary: Summary, current?: string) {
  const remaining = summary.months.filter(item => item.status !== 'complete');
  return (remaining.find(item => !current || item.month > current) || remaining.find(item => item.month !== current))?.month;
}
export function previousMonthSignature(months: MonthState[], current: string, role: 'learner' | 'coach') {
  const key = role === 'learner' ? 'student_signature' : 'coach_signature';
  const previous = months.filter(item => item.month < current && item[key])
    .sort((left, right) => right.month.localeCompare(left.month))[0];
  const signature = previous?.[key];
  return signature ? { url: signature.url, monthLabel: monthLabel(previous.month) } : undefined;
}
export function coachContact(summary?: Summary) {
  const email = summary?.learner?.coach_email?.trim();
  return email && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? `mailto:${encodeURIComponent(email)}` : undefined;
}
export function groupActivities(rows: Activity[]) {
  const groups: Record<'Attendance' | 'Activities' | 'Assignments', Activity[]> = {
    Attendance: [], Activities: [], Assignments: [],
  };
  for (const row of rows) {
    const category = row.category.trim().toLowerCase();
    const label = category === 'attendance' || row.source_ref?.startsWith('att:') ? 'Attendance'
      : category === 'assignment' ? 'Assignments' : 'Activities';
    groups[label].push(row);
  }
  return Object.entries(groups).filter(([, activities]) => activities.length > 0)
    .map(([label, activities]) => ({ label, activities }));
}
