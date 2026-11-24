import type { InputHTMLAttributes } from 'react';
import { inputClass } from '@/pages/users/components/ui';
import { formatAmountInput } from './catalogMath';

export const catalogueFocus = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-600';
export const catalogueInput = `${inputClass} min-h-10 focus-visible:ring-2 focus-visible:ring-primary-500`;

export function UnitInput({ unit, value, onValue, wholePounds = false, ...input }: {
  unit: 'GBP' | 'months';
  wholePounds?: boolean;
  value: string;
  onValue: (value: string) => void;
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'onChange' | 'onBlur'>) {
  const money = unit === 'GBP';
  return <span className="relative block">
    <input {...input} className={`${catalogueInput} ${money ? 'pl-8' : 'pr-20'} tabular-nums`}
      type={wholePounds ? 'text' : 'number'} inputMode={money && !wholePounds ? 'decimal' : 'numeric'} value={value}
      pattern={wholePounds ? '[0-9]{1,10}' : undefined} title={wholePounds ? 'Enter a whole pound amount, using digits only.' : undefined}
      onChange={event => onValue(event.currentTarget.value)}
      onBlur={money && !wholePounds ? () => onValue(formatAmountInput(value)) : undefined} />
    <span className={`pointer-events-none absolute inset-y-0 flex items-center text-xs font-semibold text-foreground-600 ${money ? 'left-3' : 'right-5'}`} aria-hidden>
      {money ? '£' : unit}
    </span>
  </span>;
}
