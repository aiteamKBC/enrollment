import { EMAIL_RE, PHONE_RE, digitsOf, formatError } from './fields.helpers';
import { useState, type ReactNode } from 'react';
import { FieldRow, inputClass } from '../../components/ui';
import { SignaturePad } from './SignaturePad';
import { FieldError, invalidClass } from '../stepErrors';
import { missingMessage, useMissing } from "../stepErrors.helpers";

export function LabeledInput({
  label,
  type = 'text',
  value,
  onChange,
  required,
  placeholder,
  helper,
  readOnly,
  missingKey,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
  placeholder?: string;
  helper?: ReactNode;
  /** Derived value — shown, but not typed into (e.g. Age, from the DOB). */
  readOnly?: boolean;
  /** This field's label in the step's validation list: red once Next is pressed while it is outstanding. */
  missingKey?: string;
}) {
  // Complain only after the learner has left the field, so an address isn't
  // marked invalid while it is still being typed — or once they press Next.
  const [touched, setTouched] = useState(false);
  const missing = useMissing(missingKey);
  // A derived value is never the learner's mistake to fix, so it is never flagged.
  const error = readOnly
    ? ''
    : ((touched || missing) && formatError(type, value)) || (missing ? missingMessage(missing, missingKey!, label, 'enter') : '');

  return (
    <FieldRow label={label} required={required}>
      <input
        type={type}
        value={value}
        readOnly={readOnly}
        // readOnly alone still leaves number inputs stepper-adjustable and the
        // field looking editable, so tab focus and the caret go too.
        tabIndex={readOnly ? -1 : undefined}
        placeholder={placeholder ?? (type === 'email' ? 'name@example.com' : type === 'tel' ? '07123 456789' : undefined)}
        inputMode={type === 'tel' ? 'tel' : type === 'email' ? 'email' : undefined}
        autoComplete={type === 'email' ? 'email' : type === 'tel' ? 'tel' : undefined}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTouched(true)}
        aria-invalid={error ? true : undefined}
        // `!` so the grey wins over inputClass's own white background — without
        // it a prefilled, read-only value looked exactly like an empty field
        // waiting to be typed into. No focus highlight either, since there is
        // nothing to type; the text can still be selected and copied.
        className={`${inputClass}${error ? invalidClass : ''}${readOnly ? ' !bg-background-200 !border-foreground-200 !text-foreground-600 cursor-default focus:!border-foreground-200 focus:!ring-0' : ''}`}
      />
      {error && <FieldError message={error} />}
      {helper && !error && <p className="text-[11px] text-foreground-400 mt-1">{helper}</p>}
    </FieldRow>
  );
}

export function LabeledSelect({
  label,
  value,
  options,
  onChange,
  required,
  placeholder = 'Select…',
  missingKey,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  required?: boolean;
  placeholder?: string;
  missingKey?: string;
}) {
  const missing = useMissing(missingKey);
  return (
    <FieldRow label={label} required={required}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={missing ? true : undefined}
        className={`${inputClass} cursor-pointer${missing ? invalidClass : ''}`}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
      {missing && <FieldError message={missingMessage(missing, missingKey!, label, 'select')} />}
    </FieldRow>
  );
}

export function LabeledTextarea({
  label,
  value,
  onChange,
  rows = 3,
  required,
  missingKey,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  required?: boolean;
  missingKey?: string;
}) {
  const missing = useMissing(missingKey);
  return (
    <FieldRow label={label} required={required}>
      <textarea
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={missing ? true : undefined}
        className={`${inputClass}${missing ? invalidClass : ''}`}
      />
      {missing && <FieldError message={missingMessage(missing, missingKey!, label, 'enter')} />}
    </FieldRow>
  );
}

/**
 * Signature capture. The value is a PNG data URL once signed — the signatory's
 * own name set in a script face (see SignaturePad). Legacy values that are
 * plain text (e.g. 'Signed digitally' from before capture existed) still render,
 * as italic text rather than an image.
 */
export function SignatureField({
  label = 'User signature',
  value,
  signatoryName,
  onChange,
  savedValue,
  missingBeforeSign,
  missingKey,
}: {
  label?: string;
  value?: string;
  signatoryName?: string;
  onChange: (v: string) => void;
  /**
   * A signature already on file for this signatory. When given, "Click to sign"
   * applies it — the signatory still signs deliberately, with one click, rather
   * than drawing the same mark again. Re-sign still opens the drawing pad.
   */
  savedValue?: string;
  /**
   * What must be answered before this can be signed. Clicking to sign while it
   * is non-empty lists it instead of signing; the list shrinks live as the
   * answers are given and disappears once nothing is outstanding.
   */
  missingBeforeSign?: string[];
  /** This signature's label in the step's validation list: red once Next is pressed unsigned. */
  missingKey?: string;
}) {
  const [editing, setEditing] = useState(false);
  const unsigned = Boolean(useMissing(missingKey)) && !value;
  const boxClass = `w-full max-w-md h-24 border-2 border-dashed rounded-lg flex flex-col items-center justify-center transition-smooth cursor-pointer hover:border-primary-300 hover:text-primary-500 ${
    unsigned ? 'border-red-500 text-red-500' : 'border-foreground-200 text-foreground-400'
  }`;
  const [triedToSign, setTriedToSign] = useState(false);
  const isImage = Boolean(value && value.startsWith('data:image/'));
  const hasSaved = Boolean(savedValue && savedValue.startsWith('data:image/'));
  const outstanding = missingBeforeSign ?? [];
  /** Sign — unless something is still unanswered, in which case say what. */
  const sign = (action: () => void) => {
    if (outstanding.length > 0) {
      setTriedToSign(true);
      return;
    }
    setTriedToSign(false);
    action();
  };

  return (
    <div className="py-2.5">
      <p className="text-[12px] text-foreground-500 font-medium mb-2">{label}</p>

      {editing ? (
        <SignaturePad
          signatoryName={signatoryName}
          onCommit={(url) => { onChange(url); setEditing(false); }}
          onCancel={() => setEditing(false)}
        />
      ) : value ? (
        <div className="flex items-center gap-3 flex-wrap">
          {isImage ? (
            <img src={value} alt={label} className="h-16 max-w-[280px] object-contain px-3 py-2 border border-foreground-200 rounded-lg bg-white" />
          ) : (
            <span className="px-4 py-6 border border-foreground-200 rounded-lg text-[13px] italic text-foreground-700 bg-background-50" style={{ fontFamily: 'cursive' }}>{value}</span>
          )}
          <button onClick={() => setEditing(true)} className="text-[12px] text-primary-600 hover:underline cursor-pointer inline-flex items-center gap-1">
            <i className="ri-pen-nib-line" />Re-sign
          </button>
          <button onClick={() => onChange('')} className="text-[12px] text-red-500 hover:underline cursor-pointer">Clear</button>
        </div>
      ) : hasSaved ? (
        <button
          type="button"
          onClick={() => sign(() => onChange(savedValue!))}
          aria-invalid={unsigned || undefined}
          className={boxClass}
        >
          <i className="ri-pen-nib-line text-2xl mb-1" />
          <span className="text-[12px]">Click to sign with your saved signature</span>
        </button>
      ) : (
        <button
          onClick={() => sign(() => setEditing(true))}
          aria-invalid={unsigned || undefined}
          className={boxClass}
        >
          <i className="ri-pen-nib-line text-2xl mb-1" />
          <span className="text-[12px]">Click to sign</span>
        </button>
      )}

      {unsigned && !editing && !(triedToSign && outstanding.length > 0) && <div><FieldError message="Please sign here." /></div>}

      {!value && triedToSign && outstanding.length > 0 && (
        <div role="alert" className="mt-2 max-w-md rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
          <p className="text-[12px] font-semibold text-amber-800">
            <i className="ri-error-warning-line mr-1" />Please answer these before signing:
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-[12px] text-amber-800">
            {outstanding.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export function StepHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-5">
      <h2 className="font-heading text-lg font-semibold tracking-tight text-foreground-900 sm:text-[20px]">{title}</h2>
      {subtitle && <p className="text-[14px] font-medium text-primary-600 mt-1">{subtitle}</p>}
    </div>
  );
}
