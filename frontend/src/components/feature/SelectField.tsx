// ============================================================================
// The app's select control: one styled, keyboard-driven dropdown to use instead
// of a native `<select>`, whose popup is drawn by the operating system and can
// neither be themed nor animated.
//
//   <SelectMenu  ... />  the bare control, for filter bars and toolbars.
//   <SelectField ... />  the same control with the label / helper / error shell
//                        every form field in the app wears.
//
// It follows the same visual and interaction rules as `DatePickerField`: a
// 42px field that turns primary when open, a portal panel so an overflowing
// list can never be clipped by a drawer, and a short origin-aware pop so the
// list reads as attached to the field it came from.
// ============================================================================

import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from '@/components/feature/AppIcon';

export interface SelectOption {
  value: string;
  label: string;
  /** Second line under the label — the detail that makes two similar options tellable apart. */
  description?: string;
  /** Remix icon class, e.g. `ri-team-line`. */
  icon?: string;
  /** Right-aligned chip: a count, a code, a level. */
  meta?: string;
  disabled?: boolean;
  /** Options carrying the same group render under one heading, in first-seen order. */
  group?: string;
}

export interface SelectMenuProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  /** Shown when nothing is selected. Also the label of the clear row when `clearable`. */
  placeholder?: string;
  disabled?: boolean;
  /** Tooltip for the disabled state, so a blocked filter can say why. */
  disabledHint?: string;
  /** Filter box. Left unset it appears once the list passes `searchThreshold`. */
  searchable?: boolean;
  searchThreshold?: number;
  searchPlaceholder?: string;
  /** Adds a row that puts the value back to ''. Pointless when the caller already ships an '' option. */
  clearable?: boolean;
  /** 'sm' matches a 40px filter bar, 'md' a 42px form field. */
  size?: 'sm' | 'md';
  error?: boolean;
  id?: string;
  ariaLabel?: string;
  ariaLabelledBy?: string;
  className?: string;
  triggerClassName?: string;
  menuClassName?: string;
  emptyMessage?: string;
  /** Floor for the panel's width, for callers whose options run longer than the trigger they open from. */
  menuMinWidth?: number;
}

const TRIGGER_HEIGHT = { sm: 'h-11', md: 'h-12' } as const;
const MENU_MAX_HEIGHT = 360;
const MENU_MIN_WIDTH = 240;
const TYPEAHEAD_RESET_MS = 900;

export function SelectMenu({
  value,
  onChange,
  options,
  placeholder = 'Select',
  disabled = false,
  disabledHint,
  searchable,
  searchThreshold = 8,
  searchPlaceholder = 'Type to filter...',
  clearable = false,
  size = 'md',
  error = false,
  id,
  ariaLabel,
  ariaLabelledBy,
  className = '',
  triggerClassName = '',
  menuClassName = '',
  emptyMessage = 'Nothing matches that',
  menuMinWidth,
}: SelectMenuProps) {
  const generatedId = useId();
  const listboxId = `select-menu-${generatedId}`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(-1);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const [above, setAbove] = useState(false);

  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const optionRefs = useRef(new Map<string, HTMLButtonElement>());
  const typeahead = useRef({ text: '', at: 0 });

  const selected = useMemo(() => options.find(option => option.value === value), [options, value]);
  const showSearch = searchable ?? options.length >= searchThreshold;

  // The clear row is a plain '' option, so keyboard navigation, the highlight and
  // the click handler all treat it like any other row.
  const rows = useMemo(() => {
    const base = clearable && !options.some(option => option.value === '')
      ? [{ value: '', label: placeholder } as SelectOption, ...options]
      : options;
    const term = query.trim().toLowerCase();
    if (!term) return base;
    return base.filter(option => (
      `${option.label} ${option.description || ''} ${option.meta || ''}`.toLowerCase().includes(term)
    ));
  }, [clearable, options, placeholder, query]);

  const nextEnabled = useCallback((from: number, direction: 1 | -1) => {
    if (!rows.length) return -1;
    let cursor = from;
    for (let step = 0; step < rows.length; step += 1) {
      cursor = (cursor + direction + rows.length) % rows.length;
      if (!rows[cursor].disabled) return cursor;
    }
    return -1;
  }, [rows]);

  const closeMenu = useCallback((refocus: boolean) => {
    setOpen(false);
    setQuery('');
    setHighlight(-1);
    if (refocus) triggerRef.current?.focus();
  }, []);

  const openMenu = useCallback((seed = '') => {
    if (disabled) return;
    setQuery(seed);
    setOpen(true);
    const selectedIndex = rows.findIndex(option => option.value === value && !option.disabled);
    setHighlight(selectedIndex >= 0 ? selectedIndex : rows.findIndex(option => !option.disabled));
  }, [disabled, rows, value]);

  const commit = useCallback((option: SelectOption) => {
    if (option.disabled) return;
    onChange(option.value);
    closeMenu(true);
  }, [closeMenu, onChange]);

  // Keep the panel pinned to the field through scrolling and resizing, flipping
  // above it when the list would otherwise run off the bottom of the viewport.
  // A layout effect (not a plain effect) so the first position is committed
  // before the browser paints -- otherwise the panel flashes at its default
  // {} style (the page's top-left corner) for a frame before jumping to the
  // trigger.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const position = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const height = Math.min(panelRef.current?.offsetHeight || MENU_MAX_HEIGHT, MENU_MAX_HEIGHT);
      const gap = 6;
      const spaceBelow = window.innerHeight - rect.bottom;
      const flip = spaceBelow < height + gap + 12 && rect.top > spaceBelow;
      const width = Math.min(Math.max(rect.width, menuMinWidth ?? MENU_MIN_WIDTH), window.innerWidth - 16);
      setAbove(flip);
      setPanelStyle({
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
        top: flip
          ? Math.max(8, rect.top - height - gap)
          : Math.min(rect.bottom + gap, window.innerHeight - 8 - Math.min(height, window.innerHeight - 16)),
        width,
        maxHeight: Math.max(120, Math.min(MENU_MAX_HEIGHT, flip ? rect.top - gap - 12 : spaceBelow - gap - 12)),
      });
    };
    position();
    const frame = window.requestAnimationFrame(position);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open, rows.length, menuMinWidth]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      closeMenu(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [closeMenu, open]);

  // Focus lands on the filter box when there is one, otherwise on the panel, so
  // the arrow keys have somewhere to be read from without the option buttons
  // stealing focus row by row (aria-activedescendant carries the highlight).
  useEffect(() => {
    if (!open) return;
    if (showSearch) searchRef.current?.focus();
    else panelRef.current?.focus();
  }, [open, showSearch]);

  useEffect(() => {
    if (!open || highlight < 0) return;
    const option = rows[highlight];
    // Optional call: jsdom (tests) has no scrollIntoView implementation.
    if (option) optionRefs.current.get(option.value)?.scrollIntoView?.({ block: 'nearest' });
  }, [highlight, open, rows]);

  // A narrowed list can leave the highlight past its end, or on a row that is
  // no longer there at all.
  useEffect(() => {
    if (!open) return;
    setHighlight(current => {
      if (current >= 0 && current < rows.length && !rows[current].disabled) return current;
      return rows.findIndex(option => !option.disabled);
    });
  }, [open, rows]);

  const jumpToTypedLabel = (char: string) => {
    const now = Date.now();
    const text = now - typeahead.current.at > TYPEAHEAD_RESET_MS ? char : typeahead.current.text + char;
    typeahead.current = { text, at: now };
    const match = rows.findIndex(option => !option.disabled && option.label.toLowerCase().startsWith(text.toLowerCase()));
    if (match >= 0) setHighlight(match);
  };

  const handleTriggerKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openMenu();
      return;
    }
    // A single printable key opens the list on what was typed, the way a native
    // select jumps to the first matching option.
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      if (showSearch) openMenu(event.key);
      else { openMenu(); jumpToTypedLabel(event.key); }
    }
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setHighlight(current => nextEnabled(current, 1));
        break;
      case 'ArrowUp':
        event.preventDefault();
        setHighlight(current => nextEnabled(current < 0 ? 0 : current, -1));
        break;
      case 'Home':
        event.preventDefault();
        setHighlight(nextEnabled(rows.length - 1, 1));
        break;
      case 'End':
        event.preventDefault();
        setHighlight(nextEnabled(0, -1));
        break;
      case 'Enter':
        event.preventDefault();
        if (rows[highlight]) commit(rows[highlight]);
        break;
      case 'Escape':
        event.preventDefault();
        closeMenu(true);
        break;
      case 'Tab':
        event.preventDefault();
        closeMenu(true);
        break;
      default:
        if (!showSearch && event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
          event.preventDefault();
          jumpToTypedLabel(event.key);
        }
    }
  };

  const triggerTone = disabled
    ? 'cursor-not-allowed border-background-200 bg-background-100 text-foreground-400'
    : open
      ? 'border-primary-300 bg-background-50 ring-2 ring-primary-100'
      : error
        ? 'border-red-300 bg-background-50'
        : 'border-background-200 bg-background-50 hover:border-primary-200';

  let lastGroup: string | undefined;

  return (
    <div className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        id={id}
        disabled={disabled}
        title={disabled ? disabledHint : undefined}
        onClick={() => (open ? closeMenu(false) : openMenu())}
        onKeyDown={handleTriggerKeyDown}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        className={`flex w-full items-center gap-2 rounded-lg border pl-3 pr-2 text-left outline-none transition-smooth ${TRIGGER_HEIGHT[size]} ${triggerTone} ${triggerClassName}`}
      >
        {selected?.icon && <AppIcon className={`${selected.icon} shrink-0 text-foreground-400`}></AppIcon>}
        <span className={`min-w-0 flex-1 truncate text-[14px] ${selected ? 'font-semibold text-foreground-900' : 'font-medium text-foreground-400'}`}>
          {selected ? selected.label : placeholder}
        </span>
        {selected?.meta && (
          <span className="shrink-0 rounded-full bg-background-100 px-2 py-0.5 text-[11px] font-bold text-foreground-500">{selected.meta}</span>
        )}
        <AppIcon className={`ri-arrow-down-s-line shrink-0 text-xl transition-transform ${disabled ? 'text-foreground-300' : 'text-foreground-400'} ${open ? 'rotate-180' : ''}`}></AppIcon>
      </button>

      {open && createPortal(
        <div
          ref={panelRef}
          tabIndex={-1}
          onKeyDown={handleMenuKeyDown}
          style={panelStyle}
          className={`fixed z-[10050] flex flex-col overflow-hidden rounded-2xl border border-background-200 bg-background-50 shadow-2xl outline-none select-menu-pop ${above ? 'is-above' : ''} ${menuClassName}`}
        >
          {showSearch && (
            <div className="flex items-center gap-2 border-b border-background-200 px-3 py-2.5">
              <AppIcon className="ri-search-line shrink-0 text-base text-foreground-300"></AppIcon>
              <input
                ref={searchRef}
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                aria-controls={listboxId}
                className="min-w-0 flex-1 !border-0 bg-transparent text-[14px] font-semibold text-foreground-900 !shadow-none !outline-none !ring-0 placeholder:font-medium placeholder:text-foreground-400"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => { setQuery(''); searchRef.current?.focus(); }}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-foreground-400 transition-smooth hover:bg-background-100 hover:text-foreground-700"
                  aria-label="Clear the filter"
                >
                  <AppIcon className="ri-close-line text-sm"></AppIcon>
                </button>
              )}
            </div>
          )}
          <div
            id={listboxId}
            role="listbox"
            aria-activedescendant={rows[highlight] ? `${listboxId}-${highlight}` : undefined}
            className="select-menu-list min-h-0 flex-1 overflow-y-auto p-1.5"
          >
            {rows.map((option, index) => {
              const active = option.value === value;
              const focused = index === highlight;
              const heading = option.group && option.group !== lastGroup ? option.group : '';
              lastGroup = option.group;
              return (
                <div key={`${option.value}-${index}`}>
                  {heading && (
                    <p className="sticky top-0 z-10 bg-background-50/95 px-3 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-foreground-400 backdrop-blur">
                      {heading}
                    </p>
                  )}
                  <button
                    ref={node => { if (node) optionRefs.current.set(option.value, node); else optionRefs.current.delete(option.value); }}
                    id={`${listboxId}-${index}`}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={active}
                    disabled={option.disabled}
                    onMouseMove={() => { if (!option.disabled && highlight !== index) setHighlight(index); }}
                    onClick={() => commit(option)}
                    className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left outline-none transition-smooth ${
                      option.disabled
                        ? 'cursor-not-allowed text-foreground-300'
                        : active
                          ? 'bg-primary-50 text-primary-800'
                          : focused
                            ? 'bg-background-100 text-foreground-900'
                            : 'text-foreground-800'
                    }`}
                  >
                    {option.icon && <AppIcon className={`${option.icon} shrink-0 text-lg ${active ? 'text-primary-600' : 'text-foreground-400'}`}></AppIcon>}
                    <span className="min-w-0 flex-1">
                      <span className="block whitespace-normal break-words text-[14px] font-semibold">{option.label}</span>
                      {option.description && (
                        <span className="mt-0.5 block whitespace-normal break-words text-[12px] font-medium text-foreground-400">{option.description}</span>
                      )}
                    </span>
                    {option.meta && (
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${active ? 'bg-primary-100 text-primary-700' : 'bg-background-100 text-foreground-500'}`}>
                        {option.meta}
                      </span>
                    )}
                    {active && <AppIcon className="ri-check-line shrink-0 text-lg text-primary-600"></AppIcon>}
                  </button>
                </div>
              );
            })}
            {!rows.length && (
              <p className="px-2.5 py-6 text-center text-[12px] font-semibold text-foreground-400">{emptyMessage}</p>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export interface SelectFieldProps extends Omit<SelectMenuProps, 'error' | 'ariaLabelledBy'> {
  label: string;
  required?: boolean;
  helper?: string;
  /** Message shown under the field; also turns the border red. */
  error?: string;
}

export function SelectField({ label, required, helper, error, ...menu }: SelectFieldProps) {
  const generatedId = useId();
  const labelId = `select-field-label-${generatedId}`;
  return (
    <div className="block">
      <span id={labelId} className="text-[10px] font-bold uppercase text-foreground-400">
        {label}{required ? ' *' : ''}
      </span>
      <div className="mt-1">
        <SelectMenu {...menu} ariaLabelledBy={labelId} error={Boolean(error)} />
      </div>
      {error
        ? <p className="mt-1 text-[11px] font-medium text-red-600">{error}</p>
        : helper ? <p className="mt-1 text-[11px] text-foreground-400">{helper}</p> : null}
    </div>
  );
}
