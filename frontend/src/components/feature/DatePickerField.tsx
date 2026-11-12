import { type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from '@/components/feature/AppIcon';

interface DatePickerFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  placeholder?: string;
  disabled?: boolean;
  min?: string;
  max?: string;
  error?: string;
  helper?: string;
  /** Non-blocking notice — shown in place of `helper` without disabling the picked date. */
  warning?: string;
  /** Use an existing visible label outside this component. */
  hideLabel?: boolean;
  inputId?: string;
  className?: string;
  autoComplete?: string;
}

type PanelView = 'days' | 'months' | 'years';

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];
const MONTH_ABBR = MONTH_NAMES.map(month => month.slice(0, 3));
const WEEKDAY_ABBR = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const YEARS_PER_PAGE = 12;

export function DatePickerField({
  label,
  value,
  onChange,
  required,
  placeholder = 'dd/mm/yyyy',
  disabled,
  min,
  max,
  error,
  helper,
  warning,
  hideLabel = false,
  inputId,
  className = '',
  autoComplete = 'off',
}: DatePickerFieldProps) {
  const generatedId = useId();
  const resolvedId = inputId ?? generatedId;
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<PanelView>('days');
  const [viewDate, setViewDate] = useState<Date>(() => startOfMonth(dateFromInput(value) || new Date()));
  const [yearPageStart, setYearPageStart] = useState(() => (dateFromInput(value) || new Date()).getFullYear() - 5);
  const [text, setText] = useState(() => formatDatePickerValue(value));
  const [typedError, setTypedError] = useState('');
  const [focusedDate, setFocusedDate] = useState<Date>(() => dateFromInput(value) || new Date());
  const [gridFocusActive, setGridFocusActive] = useState(false);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});

  const inputRef = useRef<HTMLInputElement | null>(null);
  const fieldRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const dayRefs = useRef(new Map<string, HTMLButtonElement>());
  // Set while focus is moved back to the input programmatically, so the resulting
  // focus event does not immediately reopen the panel we just closed.
  const suppressOpenRef = useRef(false);

  const minDate = dateFromInput(min || '');
  const maxDate = dateFromInput(max || '');

  // Keep the text field in sync with the committed value, but never while the user
  // is mid-keystroke — the blur/Enter handler normalises what they typed instead.
  useEffect(() => {
    if (typeof document !== 'undefined' && document.activeElement === inputRef.current) return;
    setText(formatDatePickerValue(value));
    setTypedError('');
  }, [value]);

  useEffect(() => {
    const selected = dateFromInput(value);
    if (!selected) return;
    setViewDate(startOfMonth(selected));
    setFocusedDate(selected);
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const updatePosition = () => {
      const rect = fieldRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.max(320, Math.min(380, rect.width + 64));
      const panelHeight = panelRef.current?.offsetHeight || 360;
      const gap = 8;
      const left = Math.min(Math.max(12, rect.left), window.innerWidth - width - 12);
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const shouldOpenAbove = spaceBelow < panelHeight + gap && spaceAbove > spaceBelow;
      const top = shouldOpenAbove
        ? Math.max(12, rect.top - panelHeight - gap)
        : Math.min(rect.bottom + gap, window.innerHeight - panelHeight - 12);
      setPanelStyle({ left, top: Math.max(12, top), width });
    };
    updatePosition();
    const animationFrame = window.requestAnimationFrame(updatePosition);
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [open, view]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      const target = event.target as Node;
      if (fieldRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  useEffect(() => {
    if (!open) {
      setGridFocusActive(false);
      setView('days');
    }
  }, [open]);

  // Roving focus: only steal focus into the day grid once the user has actually
  // stepped into it with the keyboard, so opening the panel leaves the caret typing.
  useEffect(() => {
    if (!open || view !== 'days' || !gridFocusActive) return;
    dayRefs.current.get(toDateInput(focusedDate))?.focus();
  }, [open, view, gridFocusActive, focusedDate]);

  const isDateDisabled = (date: Date) => (
    Boolean(minDate && date < minDate) ||
    Boolean(maxDate && date > maxDate)
  );

  const isMonthDisabled = (year: number, month: number) => (
    isDateDisabled(new Date(year, month, 1)) && isDateDisabled(endOfMonth(new Date(year, month, 1)))
  );

  const isYearDisabled = (year: number) => (
    isDateDisabled(new Date(year, 0, 1)) && isDateDisabled(new Date(year, 11, 31))
  );

  const openPanel = () => {
    if (disabled) return;
    setOpen(true);
    setView('days');
  };

  const closePanel = (returnFocus = false) => {
    setOpen(false);
    setGridFocusActive(false);
    // Only arm the suppression flag when focus is actually going to move, otherwise
    // it survives unconsumed and swallows the next genuine focus.
    if (returnFocus && document.activeElement !== inputRef.current) {
      suppressOpenRef.current = true;
      inputRef.current?.focus();
    }
  };

  /** Moves the visible month and drags the roving keyboard focus along with it. */
  const goToMonth = (year: number, month: number) => {
    const next = new Date(year, month, 1);
    setViewDate(next);
    setFocusedDate(current => addMonths(next, 0, current.getDate()));
  };

  const commitText = (raw: string, { close = false } = {}) => {
    const trimmed = raw.trim();
    if (!trimmed) {
      inputRef.current?.setCustomValidity('');
      setTypedError('');
      setText('');
      if (value) onChange('');
      if (close) closePanel(true);
      return;
    }
    const parsed = parseCompleteDate(trimmed);
    if (!parsed) {
      const message = dateEntryError(trimmed) || 'Enter a complete date as dd/mm/yyyy';
      inputRef.current?.setCustomValidity(message);
      setTypedError(message);
      return;
    }
    if (minDate && parsed < minDate) {
      inputRef.current?.setCustomValidity('Date is before the allowed minimum');
      setTypedError(`Cannot be before ${formatDatePickerValue(min || '')}`);
      return;
    }
    if (maxDate && parsed > maxDate) {
      inputRef.current?.setCustomValidity('Date is after the allowed maximum');
      setTypedError(`Cannot be after ${formatDatePickerValue(max || '')}`);
      return;
    }
    setTypedError('');
    inputRef.current?.setCustomValidity('');
    setText(formatDate(parsed));
    setViewDate(startOfMonth(parsed));
    setFocusedDate(parsed);
    onChange(toDateInput(parsed));
    if (close) closePanel(true);
  };

  const handleTextChange = (raw: string, caret = raw.length) => {
    const next = formatTypedDate(raw);
    setText(next);
    if (value && next !== formatDatePickerValue(value)) onChange('');
    const parsed = parseCompleteDate(next);
    const error = dateEntryError(next)
      || (parsed && minDate && parsed < minDate ? `Cannot be before ${formatDatePickerValue(min || '')}` : '')
      || (parsed && maxDate && parsed > maxDate ? `Cannot be after ${formatDatePickerValue(max || '')}` : '');
    setTypedError(error);
    inputRef.current?.setCustomValidity(error || (next && !parsed ? 'Enter a complete date as dd/mm/yyyy' : ''));
    if (inputRef.current === document.activeElement) {
      const nextCaret = Math.min(formatTypedDate(raw.slice(0, caret)).length, next.length);
      window.requestAnimationFrame(() => inputRef.current?.setSelectionRange(nextCaret, nextCaret));
    }
    if (!open) setOpen(true);
    setView('days');
    setGridFocusActive(false);
    if (!parsed || error) return;
    setViewDate(startOfMonth(parsed));
    setFocusedDate(parsed);
    onChange(toDateInput(parsed));
  };

  const selectDate = (date: Date, returnFocus = false) => {
    if (isDateDisabled(date)) return;
    inputRef.current?.setCustomValidity('');
    setTypedError('');
    setText(formatDate(date));
    setViewDate(startOfMonth(date));
    setFocusedDate(date);
    onChange(toDateInput(date));
    closePanel(returnFocus);
  };

  const moveMonth = (amount: number) => {
    const next = addMonths(viewDate, amount);
    goToMonth(next.getFullYear(), next.getMonth());
  };

  const moveFocus = (days: number) => {
    setGridFocusActive(true);
    setFocusedDate(current => {
      const next = addDays(current, days);
      setViewDate(startOfMonth(next));
      return next;
    });
  };

  const moveFocusByMonths = (months: number) => {
    setGridFocusActive(true);
    setFocusedDate(current => {
      const next = addMonths(current, months, current.getDate());
      setViewDate(startOfMonth(next));
      return next;
    });
  };

  const handleInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    const caret = event.currentTarget.selectionStart;
    if (event.key === 'Backspace' && caret !== null && caret === event.currentTarget.selectionEnd && caret > 1 && text[caret - 1] === '/') {
      event.preventDefault();
      handleTextChange(`${text.slice(0, caret - 2)}/${text.slice(caret)}`, caret - 2);
      return;
    }
    if (event.key === 'Delete' && caret !== null && caret === event.currentTarget.selectionEnd && text[caret] === '/') {
      event.preventDefault();
      handleTextChange(`${text.slice(0, caret + 1)}${text.slice(caret + 2)}`, caret + 1);
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      commitText(text, { close: true });
      return;
    }
    if (event.key === 'Escape') {
      if (open) {
        event.preventDefault();
        closePanel();
      }
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        openPanel();
        return;
      }
      setGridFocusActive(true);
      setFocusedDate(current => {
        const anchor = dateFromInput(value) || current;
        setViewDate(startOfMonth(anchor));
        return anchor;
      });
    }
  };

  const handleGridKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case 'ArrowLeft': moveFocus(-1); break;
      case 'ArrowRight': moveFocus(1); break;
      case 'ArrowUp': moveFocus(-7); break;
      case 'ArrowDown': moveFocus(7); break;
      case 'Home': moveFocus(-focusedDate.getDay()); break;
      case 'End': moveFocus(6 - focusedDate.getDay()); break;
      case 'PageUp': moveFocusByMonths(event.shiftKey ? -12 : -1); break;
      case 'PageDown': moveFocusByMonths(event.shiftKey ? 12 : 1); break;
      case 'Enter':
      case ' ':
        selectDate(focusedDate, true);
        break;
      case 'Escape':
        closePanel(true);
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  const monthLabel = MONTH_NAMES[viewDate.getMonth()];
  const yearLabel = viewDate.getFullYear();
  const calendarDays = buildCalendarDays(viewDate);
  const todayValue = toDateInput(new Date());
  const prevMonthDisabled = Boolean(minDate && endOfMonth(addMonths(viewDate, -1)) < minDate);
  const nextMonthDisabled = Boolean(maxDate && startOfMonth(addMonths(viewDate, 1)) > maxDate);
  const yearPage = Array.from({ length: YEARS_PER_PAGE }, (_, index) => yearPageStart + index);

  const stepperClass = 'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-foreground-500 transition-smooth hover:bg-background-100 hover:text-primary-700 disabled:cursor-not-allowed disabled:text-foreground-200 disabled:hover:bg-transparent';
  const chipClass = 'flex items-center gap-1 rounded-lg px-2 py-1 text-[13px] font-heading font-bold text-foreground-950 transition-smooth hover:bg-primary-50 hover:text-primary-700';

  const picker = open && typeof document !== 'undefined' ? createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label={`Choose ${label.toLowerCase()}`}
      className="fixed z-[10050] overflow-hidden rounded-2xl border border-background-200 bg-background-50 shadow-2xl animate-in fade-in slide-in-from-bottom-2 duration-200"
      style={panelStyle}
    >
      <div className="border-b border-background-200 px-3 py-2">
        <div className="flex items-center justify-between gap-1">
          <button
            type="button"
            onClick={() => (view === 'years' ? setYearPageStart(current => current - YEARS_PER_PAGE) : moveMonth(-1))}
            disabled={view === 'days' && prevMonthDisabled}
            className={stepperClass}
            aria-label={view === 'years' ? 'Previous years' : 'Previous month'}
          >
            <AppIcon className="ri-arrow-left-s-line text-xl"></AppIcon>
          </button>

          {view === 'years' ? (
            <p className="text-[13px] font-heading font-bold text-foreground-950">{yearPage[0]} – {yearPage[yearPage.length - 1]}</p>
          ) : (
            <div className="flex min-w-0 items-center gap-1">
              <button
                type="button"
                onClick={() => setView(current => (current === 'months' ? 'days' : 'months'))}
                className={chipClass}
                aria-label="Choose month"
              >
                {monthLabel}
                <AppIcon className={`ri-arrow-down-s-line text-base text-foreground-400 transition-transform ${view === 'months' ? 'rotate-180' : ''}`}></AppIcon>
              </button>
              <button
                type="button"
                onClick={() => {
                  setYearPageStart(viewDate.getFullYear() - 5);
                  setView('years');
                }}
                className={chipClass}
                aria-label="Choose year"
              >
                {yearLabel}
                <AppIcon className="ri-arrow-down-s-line text-base text-foreground-400"></AppIcon>
              </button>
            </div>
          )}

          <button
            type="button"
            onClick={() => (view === 'years' ? setYearPageStart(current => current + YEARS_PER_PAGE) : moveMonth(1))}
            disabled={view === 'days' && nextMonthDisabled}
            className={stepperClass}
            aria-label={view === 'years' ? 'Next years' : 'Next month'}
          >
            <AppIcon className="ri-arrow-right-s-line text-xl"></AppIcon>
          </button>
        </div>
      </div>

      {view === 'days' ? (
        <div className="p-3">
          <div className="grid grid-cols-7 gap-1 px-1 pb-2">
            {WEEKDAY_ABBR.map(day => (
              <span key={day} className="text-center text-[10px] font-bold uppercase text-foreground-400">{day}</span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1" role="grid" onKeyDown={handleGridKeyDown}>
            {calendarDays.map(day => {
              const dayValue = toDateInput(day.date);
              const selected = value === dayValue;
              const today = todayValue === dayValue;
              const dayDisabled = isDateDisabled(day.date);
              return (
                <button
                  key={dayValue}
                  ref={node => {
                    if (node) dayRefs.current.set(dayValue, node);
                    else dayRefs.current.delete(dayValue);
                  }}
                  type="button"
                  disabled={dayDisabled}
                  tabIndex={isSameDay(day.date, focusedDate) ? 0 : -1}
                  aria-selected={selected}
                  aria-current={today ? 'date' : undefined}
                  aria-label={day.date.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                  onClick={() => selectDate(day.date)}
                  className={`flex h-9 items-center justify-center rounded-lg text-[12px] font-bold outline-none transition-smooth focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:text-foreground-200 disabled:hover:bg-transparent ${selected ? 'bg-primary-600 text-white shadow-sm' : day.inCurrentMonth ? 'text-foreground-900 hover:bg-primary-50 hover:text-primary-700' : 'text-foreground-300 hover:bg-background-100'} ${today && !selected ? 'ring-1 ring-primary-200' : ''}`}
                >
                  {day.date.getDate()}
                </button>
              );
            })}
          </div>
        </div>
      ) : view === 'months' ? (
        <div className="grid grid-cols-3 gap-1 p-3">
          {MONTH_ABBR.map((month, index) => {
            const active = index === viewDate.getMonth();
            const monthDisabled = isMonthDisabled(viewDate.getFullYear(), index);
            return (
              <button
                key={month}
                type="button"
                disabled={monthDisabled}
                aria-label={MONTH_NAMES[index]}
                onClick={() => {
                  goToMonth(viewDate.getFullYear(), index);
                  setView('days');
                }}
                className={`flex h-11 items-center justify-center rounded-lg text-[12px] font-bold outline-none transition-smooth focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:text-foreground-200 disabled:hover:bg-transparent ${active ? 'bg-primary-600 text-white shadow-sm' : 'text-foreground-900 hover:bg-primary-50 hover:text-primary-700'}`}
              >
                {month}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="grid grid-cols-4 gap-1 p-3">
          {yearPage.map(year => {
            const active = year === viewDate.getFullYear();
            const yearDisabled = isYearDisabled(year);
            return (
              <button
                key={year}
                type="button"
                disabled={yearDisabled}
                onClick={() => {
                  goToMonth(year, viewDate.getMonth());
                  setView('months');
                }}
                className={`flex h-11 items-center justify-center rounded-lg text-[12px] font-bold outline-none transition-smooth focus-visible:ring-2 focus-visible:ring-primary-400 disabled:cursor-not-allowed disabled:text-foreground-200 disabled:hover:bg-transparent ${active ? 'bg-primary-600 text-white shadow-sm' : 'text-foreground-900 hover:bg-primary-50 hover:text-primary-700'}`}
              >
                {year}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-between border-t border-background-200 bg-background-100/70 px-3 py-2">
        <button
          type="button"
          onClick={() => {
            setText('');
            setTypedError('');
            inputRef.current?.setCustomValidity('');
            onChange('');
            closePanel(true);
          }}
          className="rounded-lg px-3 py-1.5 text-[11px] font-bold text-foreground-500 transition-smooth hover:bg-background-50 hover:text-red-600"
        >
          Clear
        </button>
        <button
          type="button"
          onClick={() => selectDate(new Date(), true)}
          disabled={isDateDisabled(new Date())}
          className="rounded-lg bg-primary-50 px-3 py-1.5 text-[11px] font-bold text-primary-700 transition-smooth hover:bg-primary-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Today
        </button>
      </div>
    </div>,
    document.body,
  ) : null;

  const shownError = typedError || error;

  return (
    <div className={`block ${className}`}>
      {!hideLabel && <span className="text-[10px] font-bold uppercase text-foreground-400">{label}{required ? ' *' : ''}</span>}
      <div
        ref={fieldRef}
        className={`mt-1 flex h-[42px] w-full items-center gap-2 rounded-lg border bg-background-50 pl-3 pr-1 transition-smooth ${disabled ? 'cursor-not-allowed bg-background-100' : ''} ${shownError ? 'border-red-300 ring-2 ring-red-100' : open ? 'border-primary-300 ring-2 ring-primary-100' : 'border-background-200 hover:border-primary-200'}`}
      >
        <AppIcon className="ri-calendar-event-line shrink-0 text-foreground-400"></AppIcon>
        <input
          ref={inputRef}
          id={resolvedId}
          type="text"
          inputMode="numeric"
          autoComplete={autoComplete}
          required={required}
          aria-invalid={Boolean(shownError) || undefined}
          aria-describedby={shownError ? `${resolvedId}-error` : undefined}
          role="combobox"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={label}
          value={text}
          placeholder={placeholder}
          disabled={disabled}
          onChange={event => handleTextChange(event.target.value, event.target.selectionStart ?? event.target.value.length)}
          onFocus={() => {
            if (suppressOpenRef.current) {
              suppressOpenRef.current = false;
              return;
            }
            openPanel();
          }}
          onClick={openPanel}
          onBlur={() => commitText(text)}
          onKeyDown={handleInputKeyDown}
          className="min-w-0 flex-1 bg-transparent text-[13px] font-semibold text-foreground-900 outline-none placeholder:font-medium placeholder:text-foreground-400 disabled:cursor-not-allowed disabled:text-foreground-500"
        />
        <button
          type="button"
          tabIndex={-1}
          disabled={disabled}
          onClick={() => (open ? closePanel(true) : openPanel())}
          aria-label={open ? 'Close calendar' : 'Open calendar'}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-foreground-400 transition-smooth hover:bg-background-100 hover:text-primary-700 disabled:cursor-not-allowed disabled:hover:bg-transparent"
        >
          <AppIcon className={`ri-arrow-down-s-line text-lg transition-transform ${open ? 'rotate-180' : ''}`}></AppIcon>
        </button>
      </div>
      {picker}
      {shownError ? (
        <p id={`${resolvedId}-error`} className="mt-1 text-[11px] font-medium text-red-600">{shownError}</p>
      ) : warning ? (
        <p className="mt-1 flex items-center gap-1 text-[11px] font-semibold text-amber-700">
          <AppIcon className="ri-alert-line shrink-0 text-[12px]"></AppIcon>
          {warning}
        </p>
      ) : helper ? (
        <p className="mt-1 text-[11px] text-foreground-400">{helper}</p>
      ) : null}
    </div>
  );
}

function parseDateParts(dateValue: string) {
  const [year, month, day] = String(dateValue || '').split('-').map(Number);
  if (!year || !month || !day) return null;
  return { year, month, day };
}

function dateFromInput(dateValue: string) {
  const parts = parseDateParts(dateValue);
  return parts ? new Date(parts.year, parts.month - 1, parts.day) : null;
}

function toDateInput(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDate(date: Date) {
  return formatDatePickerValue(toDateInput(date));
}

function formatDatePickerValue(value: string) {
  const parts = parseDateParts(value);
  if (!parts) return '';
  return `${String(parts.day).padStart(2, '0')}/${String(parts.month).padStart(2, '0')}/${parts.year}`;
}

function startOfMonth(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function endOfMonth(date: Date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 0);
}

function addDays(date: Date, amount: number) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + amount);
}

/** Month arithmetic that clamps the day instead of rolling into the next month. */
function addMonths(date: Date, amount: number, preferredDay = 1) {
  const target = new Date(date.getFullYear(), date.getMonth() + amount, 1);
  const lastDay = endOfMonth(target).getDate();
  target.setDate(Math.min(preferredDay, lastDay));
  return target;
}

function isSameDay(left: Date, right: Date) {
  return toDateInput(left) === toDateInput(right);
}

function buildCalendarDays(viewDate: Date) {
  const monthStart = startOfMonth(viewDate);
  const cursor = new Date(monthStart);
  cursor.setDate(cursor.getDate() - cursor.getDay());
  return Array.from({ length: 42 }, () => {
    const date = new Date(cursor);
    cursor.setDate(cursor.getDate() + 1);
    return {
      date,
      inCurrentMonth: date.getMonth() === viewDate.getMonth(),
    };
  });
}

function buildDate(year: number, month: number, day: number) {
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  const date = new Date(year, month - 1, day);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return date;
}

/** Keep a single text box, with two day digits, two month digits and four year digits. */
function formatTypedDate(raw: string): string {
  const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;

  const parts = raw.replace(/[^\d/]/g, '').split('/');
  if (parts.length > 1) {
    const day = parts[0].slice(0, 2);
    const monthDigits = parts[0].slice(2) + parts[1];
    const month = monthDigits.slice(0, 2);
    const year = (monthDigits.slice(2) + parts.slice(2).join('')).slice(0, 4);
    return `${day}/${month}${parts.length > 2 || year ? `/${year}` : ''}`;
  }
  const digits = parts[0].slice(0, 8);
  return digits.length > 4
    ? `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`
    : digits.length > 2 ? `${digits.slice(0, 2)}/${digits.slice(2)}` : digits;
}

function parseCompleteDate(text: string): Date | null {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(text);
  return match ? buildDate(Number(match[3]), Number(match[2]), Number(match[1])) : null;
}

function dateEntryError(text: string): string {
  if (!text) return '';
  const [day, month, year] = text.split('/');
  if (day.length === 2 && (Number(day) < 1 || Number(day) > 31)) return 'Day must be between 01 and 31';
  if (month?.length === 2 && (Number(month) < 1 || Number(month) > 12)) return 'Month must be between 01 and 12';
  if (day.length === 2 && month?.length === 2 && year?.length === 4 && !parseCompleteDate(text)) return 'Enter a real calendar date';
  return '';
}
