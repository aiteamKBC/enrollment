import { useEffect, useRef, useState, type InputHTMLAttributes, type Ref } from 'react';
import { ChevronDown, Clock3 } from 'lucide-react';
import { btnSecondary } from '@/pages/users/components/ui';
import { catalogueFocus, catalogueInput } from './catalogControls';

const timePattern = '(?:[01][0-9]|2[0-3]):[0-5][0-9]';
const validTime = new RegExp(`^${timePattern}$`);
const numbers = (count: number) => Array.from({ length: count }, (_, index) => String(index).padStart(2, '0'));
const hours = numbers(24);
const minutes = numbers(60);

function normalizedPart(value: string, options: string[]) {
  const padded = value.padStart(2, '0');
  return value && options.includes(padded) ? padded : '';
}

function TimePart({ id, label, part, options, value, onValue, inputRef }: {
  id: string; label: string; part: 'Hour' | 'Minute'; options: string[]; value: string;
  onValue: (value: string) => void; inputRef?: Ref<HTMLInputElement>;
}) {
  const fieldLabel = `${label} ${part.toLowerCase()}`;
  const selected = normalizedPart(value, options);
  const invalid = Boolean(value && !selected);
  return <div className="min-w-0 space-y-1 text-xs font-medium">
    <label htmlFor={id}>{part}</label>
    <div className="relative">
      <input ref={inputRef} id={id} aria-label={fieldLabel} aria-invalid={invalid}
        aria-describedby={invalid ? `${id}-range` : undefined} inputMode="numeric" type="text"
        className={`${catalogueInput} pr-10 tabular-nums`} value={value} maxLength={2} placeholder="00"
        onChange={event => onValue(event.currentTarget.value.replace(/[^0-9]/g, ''))} />
      <div className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-lg text-foreground-700 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary-500">
        <ChevronDown size={16} aria-hidden />
        <select aria-label={`Choose ${fieldLabel.toLowerCase()}`} value={selected}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
          onChange={event => onValue(event.currentTarget.value)}>
          <option value="" disabled>{part}</option>
          {options.map(option => <option key={option}>{option}</option>)}
        </select>
      </div>
    </div>
    {invalid && <p id={`${id}-range`} className="text-red-800">Enter 00 to {options.at(-1)}.</p>}
  </div>;
}

type TimeInputProps = Pick<InputHTMLAttributes<HTMLInputElement>, 'name' | 'required' | 'aria-describedby'> & {
  id: string;
  label: string;
  value: string;
  onValue: (value: string) => void;
};

export function CatalogTimeInput({ id, label, value, onValue, ...input }: TimeInputProps) {
  const [selection, setSelection] = useState<{ hour: string; minute: string } | null>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const hourControl = useRef<HTMLInputElement>(null);
  const open = selection !== null;
  const panelId = `${id}-picker`;
  const labelLower = label.toLowerCase();
  const hour = normalizedPart(selection?.hour ?? '', hours);
  const minute = normalizedPart(selection?.minute ?? '', minutes);

  useEffect(() => {
    if (open) hourControl.current?.focus();
  }, [open]);

  function closePicker() {
    setSelection(null);
    toggle.current?.focus();
  }

  function applyTime() {
    if (!hour || !minute) return;
    onValue(`${hour}:${minute}`);
    closePicker();
  }

  return (
    <div className="space-y-2" onKeyDown={event => {
      if (open && event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault();
        closePicker();
      }
    }} onBlur={event => {
      if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setSelection(null);
    }}>
      <div className="flex items-stretch gap-2">
        <input {...input} id={id} type="text" className={`${catalogueInput} min-w-0 tabular-nums`}
          placeholder="HH:mm" pattern={timePattern} maxLength={5} value={value} inputMode="numeric"
          title="Enter a 24-hour time between 00:00 and 23:59."
          onChange={event => {
            setSelection(null);
            const typed = event.currentTarget.value;
            const deleting = (event.nativeEvent as InputEvent).inputType?.startsWith('delete');
            onValue(!deleting && /^\d{4}$/.test(typed) ? `${typed.slice(0, 2)}:${typed.slice(2)}` : typed);
          }} />
        <button ref={toggle} type="button" aria-label={`Choose ${labelLower}`} aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          className={`grid min-w-11 shrink-0 cursor-pointer place-items-center rounded-lg border border-foreground-200 bg-background-50 text-foreground-700 hover:border-primary-400 hover:text-primary-700 disabled:cursor-not-allowed ${catalogueFocus}`}
          onClick={() => {
            if (open) {
              closePicker();
              return;
            }
            const [hour, minute] = validTime.test(value) ? value.split(':') : ['', ''];
            setSelection({ hour, minute });
          }}><Clock3 size={18} aria-hidden /></button>
      </div>
      {selection && <div id={panelId} className="space-y-3 rounded-lg border border-background-200 bg-background-100 p-3"
        onKeyDown={event => {
          if (event.key === 'Enter' && event.target instanceof HTMLInputElement) {
            event.preventDefault();
            applyTime();
          }
        }}>
        <div className="grid grid-cols-2 gap-2">
          <TimePart id={`${id}-hour`} label={label} part="Hour" options={hours} value={selection.hour}
            inputRef={hourControl} onValue={hour => setSelection({ ...selection, hour })} />
          <TimePart id={`${id}-minute`} label={label} part="Minute" options={minutes} value={selection.minute}
            onValue={minute => setSelection({ ...selection, minute })} />
        </div>
        <button type="button" className={`${btnSecondary} ${catalogueFocus} disabled:cursor-not-allowed disabled:opacity-50`}
          disabled={!hour || !minute} onClick={applyTime}>Set {labelLower}</button>
      </div>}
    </div>
  );
}
