import { useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';

export function BookingDatePicker({ value, onChange, dates }: { value: string; onChange: (value: string) => void; dates: string[] }) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState('');
  const shown = month || value.slice(0, 7) || dates[0]?.slice(0, 7) || new Date().toISOString().slice(0, 7);
  const [year, number] = shown.split('-').map(Number);
  const first = new Date(Date.UTC(year, number - 1, 1));
  const count = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const shift = (offset: number) => setMonth(new Date(Date.UTC(year, number - 1 + offset, 1)).toISOString().slice(0, 7));
  return <div className="relative">
    <span className="block">Date</span>
    <button type="button" aria-label="Date" aria-expanded={open} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-3 text-left text-sm" onClick={() => { setMonth(value.slice(0, 7) || dates[0]?.slice(0, 7) || ''); setOpen(!open); }}>{value || 'Select a date'} <CalendarDays aria-hidden="true" className="float-right h-4 w-4 text-slate-500" /></button>
    {open && <div role="group" aria-label="Booking calendar" data-month={shown} onKeyDown={event => { if (event.key === 'Escape') setOpen(false); }} className="absolute left-0 top-full z-20 mt-2 w-80 max-w-full rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
      <div className="mb-3 flex items-center justify-between">
        <button type="button" aria-label="Previous month" disabled={shown <= (dates[0]?.slice(0, 7) || shown)} onClick={() => shift(-1)} className="p-2 disabled:opacity-30"><ChevronLeft aria-hidden="true" className="h-4 w-4" /></button>
        <strong aria-live="polite">{first.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })}</strong>
        <button type="button" aria-label="Next month" disabled={shown >= (dates.at(-1)?.slice(0, 7) || shown)} onClick={() => shift(1)} className="p-2 disabled:opacity-30"><ChevronRight aria-hidden="true" className="h-4 w-4" /></button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-sm">
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(day => <span key={day} className="text-xs text-slate-500">{day}</span>)}
        {Array.from({ length: (first.getUTCDay() + 6) % 7 }, (_, i) => <span key={`blank-${i}`} />)}
        {Array.from({ length: count }, (_, i) => {
          const date = `${shown}-${String(i + 1).padStart(2, '0')}`;
          return <button type="button" key={date} aria-label={date} aria-pressed={date === value} disabled={!dates.includes(date)} className={`rounded-lg py-2 disabled:cursor-not-allowed disabled:text-slate-300 ${date === value ? 'bg-slate-900 text-white' : 'enabled:hover:bg-blue-50'}`} onClick={() => { onChange(date); setOpen(false); }}>{i + 1}</button>;
        })}
      </div>
      <div className="mt-3 flex justify-between text-sm"><button type="button" onClick={() => { onChange(''); setOpen(false); }}>Clear date</button><button type="button" onClick={() => setOpen(false)}>Close calendar</button></div>
    </div>}
  </div>;
}
