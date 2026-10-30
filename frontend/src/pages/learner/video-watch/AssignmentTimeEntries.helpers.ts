import type { MonthlyAssignment } from '@/api/monthlyAssignment';
import { type BookingCalendarRules } from '@/api/learnerCalendar';


export function assignmentDateRestriction(date: string, rules: BookingCalendarRules | null) {
  const day = new Date(`${date}T12:00:00Z`).getUTCDay();
  if (day === 0 || day === 6) return 'Weekend';
  if (!rules || !rules.coveredYears.includes(Number(date.slice(0, 4)))) return 'Holiday calendar unavailable';
  return rules.bankHolidays.find(item => item.date === date)?.title || '';
}


export function assignmentMonthBounds(month: string) {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return null;
  const [year, number] = month.split('-').map(Number);
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate();
  return { min: `${month}-01`, max: `${month}-${days}` };
}


export function assignmentTimeHours(entries: MonthlyAssignment['timeEntries']) {
  return (entries || []).reduce((sum, row) => {
    const hours = Number(row.hours);
    return sum + (Number.isFinite(hours) && hours > 0 ? hours : 0);
  }, 0);
}
