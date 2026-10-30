import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import {
  AlertCircle, ArrowLeft, ArrowRight, Check, Eraser, ImageUp, MapPin, PenLine, RefreshCw, ShieldCheck, Trash2, Type, UserRound,
} from 'lucide-react';
import SignaturePadLib from 'signature_pad';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { RowsSkeleton } from '@/components/feature/Skeletons';
import { roleNavMap } from '@/mocks/navigation';
import { useAuth } from "@/hooks/useAuth.helpers";
import { syncLearnerStatus } from '@/hooks/useLearnerNavGate';
import { isOnboardingStatus, ONBOARDING_ROUTE } from '@/hooks/useOnboardingRedirect';
import { COUNTRY_OPTIONS, DEFAULT_COUNTRY } from '@/lib/countries';
import { createTypedSignature } from '@/lib/typedSignature';
import {
  fetchFirstLoginDetails,
  submitFirstLoginDetails,
  FirstLoginDetailsError,
  type FirstLoginDetailsState,
  type FirstLoginDetailsValues,
} from '@/api/firstLoginDetails';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';

// ============================================================================
// First sign-in for a new apprentice: their address and personal details, then
// an electronic signature, before the enrolment wizard opens.
//
// Nothing is saved until Finish, and then everything in one request — so the
// learner is never left half-way with their status moved but no signature, and
// Back between the two screens keeps every answer.
// ============================================================================

const learnerNav = roleNavMap.learner;
const TITLE_OPTIONS = ['Mr', 'Mrs', 'Miss', 'Ms', 'Mx', 'Dr', 'Prof'];
// Same loose check the server makes (first_login_details.UK_POSTCODE).
const UK_POSTCODE = /^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$/i;
const PHONE_ALLOWED = /^\+?[0-9 ()-]+$/;
const DETAIL_FIELDS: (keyof FirstLoginDetailsValues)[] = [
  'country', 'postcode', 'addressLine1', 'townCity', 'title', 'dateOfBirth', 'phone',
];

/** An uploaded picture is redrawn as a PNG no larger than this, so it fits the
 *  signature column (and its 400 KB cap) whatever the camera produced. */
const UPLOAD_MAX_WIDTH = 600;
const UPLOAD_MAX_HEIGHT = 200;
const UPLOAD_MAX_BYTES = 5 * 1024 * 1024;
const UPLOAD_TYPES = /^image\/(png|jpeg|webp)$/i;

type SignMode = 'draw' | 'choose' | 'upload';
type FieldErrors = Partial<Record<keyof FirstLoginDetailsValues | 'signature', string>>;

const fieldClass =
  'w-full h-11 px-3.5 text-[14px] bg-white border border-foreground-200 rounded-xl text-foreground-900 placeholder:text-foreground-400 '
  + 'focus:border-primary-400 focus:ring-2 focus:ring-primary-200/50 outline-none transition-smooth '
  + 'aria-[invalid=true]:border-red-400 aria-[invalid=true]:bg-red-50/40';

function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

function validateDetails(values: FirstLoginDetailsValues): FieldErrors {
  const errors: FieldErrors = {};
  if (!values.title.trim()) errors.title = 'Choose your title.';
  if (!values.dateOfBirth) errors.dateOfBirth = 'Enter your date of birth.';
  else if (values.dateOfBirth < '1900-01-01' || values.dateOfBirth >= todayIso()) errors.dateOfBirth = 'Enter a date of birth in the past.';
  const phone = values.phone.trim();
  const digits = phone.replace(/\D/g, '').length;
  if (!phone) errors.phone = 'Enter your mobile number.';
  else if (!PHONE_ALLOWED.test(phone) || digits < 7 || digits > 15) errors.phone = 'Enter a valid mobile number.';
  if (!values.country.trim()) errors.country = 'Choose your country.';
  if (values.country === DEFAULT_COUNTRY && !UK_POSTCODE.test(values.postcode.trim())) {
    errors.postcode = 'Enter a valid UK postcode, for example CT1 1AA.';
  }
  if (!values.addressLine1.trim()) errors.addressLine1 = 'Enter the first line of your address.';
  if (!values.townCity.trim()) errors.townCity = 'Enter your town or city.';
  return errors;
}

/** Redraw an uploaded signature picture as a size-capped PNG data URL. */
async function uploadedSignatureToPng(file: File): Promise<string> {
  if (!UPLOAD_TYPES.test(file.type)) throw new Error('Upload your signature as a PNG, JPEG or WebP image.');
  if (file.size > UPLOAD_MAX_BYTES) throw new Error('That image is too large. Use one under 5 MB.');
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('That file could not be read as an image.'));
      img.src = url;
    });
    const scale = Math.min(1, UPLOAD_MAX_WIDTH / image.naturalWidth, UPLOAD_MAX_HEIGHT / image.naturalHeight);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Your browser could not prepare that image. Please draw your signature instead.');
    // White behind a transparent PNG, matching the drawn signatures.
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/png');
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ---- shared pieces ----------------------------------------------------------

function RequiredMark() {
  return (
    <>
      <span aria-hidden="true" className="ml-0.5 text-red-500">*</span>
      <span className="sr-only"> (required)</span>
    </>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1.5 flex items-center gap-1 text-[12px] font-medium text-red-600">
      <AlertCircle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />{message}
    </p>
  );
}

function Field({ id, label, required, error, hint, children }: {
  id: string; label: string; required?: boolean; error?: string; hint?: string; children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[13px] font-semibold text-foreground-800">
        {label}{required && <RequiredMark />}
      </label>
      {children}
      {hint && !error && <p className="mt-1.5 text-[12px] text-foreground-500">{hint}</p>}
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}

function SectionHeading({ icon, title, text, id }: { icon: ReactNode; title: string; text: string; id: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary-600 ring-1 ring-primary-100">
        {icon}
      </span>
      <div>
        <h2 id={id} className="font-heading text-[17px] font-bold text-foreground-950">{title}</h2>
        <p className="mt-0.5 text-[13px] text-foreground-500">{text}</p>
      </div>
    </div>
  );
}

function Stepper({ step }: { step: 1 | 2 }) {
  const item = (n: 1 | 2, label: string) => {
    const done = step > n;
    const current = step === n;
    return (
      <li className="flex items-center gap-2.5" aria-current={current ? 'step' : undefined}>
        <span className={`flex h-8 w-8 items-center justify-center rounded-full text-[13px] font-bold transition-smooth ${
          done ? 'bg-primary-600 text-white' : current ? 'bg-primary-600 text-white ring-4 ring-primary-100' : 'bg-background-200 text-foreground-500'
        }`}>
          {done ? <Check aria-hidden="true" className="h-4 w-4" /> : n}
        </span>
        <span className={`text-[13px] font-semibold ${current || done ? 'text-foreground-900' : 'text-foreground-500'}`}>{label}</span>
      </li>
    );
  };
  return (
    <ol aria-label="Progress" className="flex items-center gap-3">
      {item(1, 'Your details')}
      <li aria-hidden="true" className={`h-0.5 w-10 rounded-full sm:w-16 ${step > 1 ? 'bg-primary-500' : 'bg-foreground-200'}`} />
      {item(2, 'Your signature')}
    </ol>
  );
}

// ---- Step 1: address + about you ------------------------------------------

function DetailsStep({
  values, errors, onChange, onNext,
}: {
  values: FirstLoginDetailsValues;
  errors: FieldErrors;
  onChange: (key: keyof FirstLoginDetailsValues, value: string) => void;
  onNext: () => void;
}) {
  const input = (key: keyof FirstLoginDetailsValues, extra: Record<string, unknown> = {}) => ({
    id: `fl-${key}`,
    value: values[key],
    onChange: (e: { target: { value: string } }) => onChange(key, e.target.value),
    'aria-invalid': errors[key] ? true : undefined,
    'aria-describedby': errors[key] ? `fl-${key}-error` : undefined,
    className: fieldClass,
    ...extra,
  });
  const titleOptions = values.title && !TITLE_OPTIONS.includes(values.title) ? [values.title, ...TITLE_OPTIONS] : TITLE_OPTIONS;
  const countryOptions = values.country && !COUNTRY_OPTIONS.includes(values.country) ? [values.country, ...COUNTRY_OPTIONS] : COUNTRY_OPTIONS;
  const uk = values.country === DEFAULT_COUNTRY;

  return (
    <form noValidate onSubmit={(e) => { e.preventDefault(); onNext(); }} className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="fl-address-heading" className="rounded-2xl border border-foreground-200/70 bg-background-50 p-5 shadow-sm sm:p-6">
          <SectionHeading
            id="fl-address-heading"
            icon={<MapPin aria-hidden="true" className="h-5 w-5" />}
            title="Your Address"
            text="Where you live now. We use this on your enrolment record."
          />
          <div className="mt-5 grid gap-4 sm:grid-cols-[1fr_11rem]">
            <Field id="fl-country" label="Country" required error={errors.country}>
              <select {...input('country', { autoComplete: 'country-name' })}>
                {countryOptions.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            <Field id="fl-postcode" label="Postcode" required={uk} error={errors.postcode}>
              <input {...input('postcode', { autoComplete: 'postal-code', maxLength: 10, placeholder: uk ? 'CT1 1AA' : '' })} />
            </Field>
          </div>
          <fieldset className="mt-4 space-y-3">
            <legend className="mb-1.5 text-[13px] font-semibold text-foreground-800">Home address</legend>
            <div>
              <label htmlFor="fl-addressLine1" className="sr-only">Address line 1 (required)</label>
              <div className="relative">
                <input {...input('addressLine1', { placeholder: 'Address line 1', autoComplete: 'address-line1', maxLength: 200 })} />
                <span aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-red-500">*</span>
              </div>
              <FieldError id="fl-addressLine1-error" message={errors.addressLine1} />
            </div>
            <div>
              <label htmlFor="fl-addressLine2" className="sr-only">Address line 2 (optional)</label>
              <input {...input('addressLine2', { placeholder: 'Address line 2 (optional)', autoComplete: 'address-line2', maxLength: 200 })} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="fl-townCity" className="sr-only">Town or city (required)</label>
                <div className="relative">
                  <input {...input('townCity', { placeholder: 'Town / City', autoComplete: 'address-level2', maxLength: 100 })} />
                  <span aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-red-500">*</span>
                </div>
                <FieldError id="fl-townCity-error" message={errors.townCity} />
              </div>
              <div>
                <label htmlFor="fl-county" className="sr-only">County (optional)</label>
                <input {...input('county', { placeholder: 'County (optional)', autoComplete: 'address-level1', maxLength: 100 })} />
              </div>
            </div>
          </fieldset>
        </section>

        <section aria-labelledby="fl-about-heading" className="rounded-2xl border border-foreground-200/70 bg-background-50 p-5 shadow-sm sm:p-6">
          <SectionHeading
            id="fl-about-heading"
            icon={<UserRound aria-hidden="true" className="h-5 w-5" />}
            title="About You"
            text="We will keep your personal information safe at all times."
          />
          <div className="mt-5 space-y-4">
            <div className="max-w-[12rem]">
              <Field id="fl-title" label="Title" required error={errors.title}>
                <select {...input('title', { autoComplete: 'honorific-prefix' })}>
                  <option value="">Select…</option>
                  {titleOptions.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </Field>
            </div>
            <div className="max-w-[16rem]">
              <Field id="fl-dateOfBirth" label="Date of Birth" required error={errors.dateOfBirth}>
                <input {...input('dateOfBirth', { type: 'date', max: todayIso(), min: '1900-01-01', autoComplete: 'bday' })} />
              </Field>
            </div>
            <div className="max-w-[20rem]">
              <Field id="fl-phone" label="Mobile Number" required error={errors.phone} hint="Include the country code if it isn’t a UK number.">
                <input {...input('phone', { type: 'tel', autoComplete: 'tel', inputMode: 'tel', maxLength: 30, placeholder: '07700 900123' })} />
              </Field>
            </div>
          </div>
        </section>
      </div>

      <div className="flex flex-col-reverse items-stretch gap-3 rounded-2xl border border-foreground-200/70 bg-background-50 px-5 py-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
        <p className="text-[12px] text-foreground-500">
          <span aria-hidden="true" className="text-red-500">*</span> Required
        </p>
        <button type="submit" className={`${btnPrimary} justify-center`}>
          Next <ArrowRight aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
    </form>
  );
}

// ---- Step 2: signature ------------------------------------------------------

type PadData = ReturnType<SignaturePadLib['toData']>;

/** A drawing surface whose strokes survive switching tabs and steps. */
function DrawPad({ strokes, onChange, invalid }: {
  strokes: PadData;
  onChange: (dataUrl: string, strokes: PadData) => void;
  invalid: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const padRef = useRef<SignaturePadLib | null>(null);
  const initial = useRef(strokes);
  const changed = useRef(onChange);
  changed.current = onChange;
  const [hasInk, setHasInk] = useState(strokes.length > 0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const width = canvas.getBoundingClientRect().width || 420;
    const height = canvas.getBoundingClientRect().height || 160;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.getContext('2d')?.scale(ratio, ratio);
    const pad = new SignaturePadLib(canvas, { backgroundColor: 'rgba(0,0,0,0)', penColor: 'rgb(15,23,42)' });
    if (initial.current.length) pad.fromData(initial.current);
    padRef.current = pad;
    const onEnd = () => {
      setHasInk(!pad.isEmpty());
      changed.current(pad.isEmpty() ? '' : pad.toDataURL('image/png'), pad.toData());
    };
    pad.addEventListener('endStroke', onEnd);
    return () => {
      pad.removeEventListener('endStroke', onEnd);
      pad.off();
      padRef.current = null;
    };
  }, []);

  const clear = () => {
    padRef.current?.clear();
    setHasInk(false);
    onChange('', []);
  };

  return (
    <div className="space-y-2">
      <div className={`relative h-40 w-full max-w-md overflow-hidden rounded-xl border-2 border-dashed bg-white ${invalid ? 'border-red-300' : 'border-foreground-200'}`}>
        {!hasInk && (
          <span aria-hidden="true" className="pointer-events-none absolute inset-0 flex items-center justify-center text-[13px] text-foreground-300">
            Sign here
          </span>
        )}
        <span aria-hidden="true" className="pointer-events-none absolute bottom-9 left-6 right-6 border-b border-foreground-200" />
        <canvas
          ref={canvasRef}
          aria-label="Draw your signature"
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? 'fl-signature-error' : undefined}
          className="relative h-full w-full touch-none"
        />
      </div>
      <button type="button" onClick={clear} disabled={!hasInk}
        className="inline-flex items-center gap-1.5 text-[12px] font-medium text-primary-600 hover:underline disabled:cursor-not-allowed disabled:text-foreground-300 disabled:no-underline">
        <Eraser aria-hidden="true" className="h-3.5 w-3.5" />Clear
      </button>
    </div>
  );
}

function UploadPanel({ uploaded, onUpload, onRemove, invalid }: {
  uploaded: string;
  onUpload: (file: File) => void;
  onRemove: () => void;
  invalid: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        id="fl-signature-upload"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="sr-only"
        aria-describedby={invalid ? 'fl-signature-error' : 'fl-signature-upload-hint'}
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Cleared so choosing the same file again still fires a change.
          e.target.value = '';
          if (file) onUpload(file);
        }}
      />
      {uploaded ? (
        <div className="flex h-40 w-full max-w-md items-center justify-center rounded-xl border-2 border-primary-300 bg-white p-3">
          <img src={uploaded} alt="Your uploaded signature" className="max-h-full max-w-full object-contain" />
        </div>
      ) : (
        <label htmlFor="fl-signature-upload"
          className={`flex h-40 w-full max-w-md cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed bg-white text-center transition-smooth hover:border-primary-400 hover:bg-primary-50/30 ${invalid ? 'border-red-300' : 'border-foreground-200'}`}>
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary-50 text-primary-600">
            <ImageUp aria-hidden="true" className="h-5 w-5" />
          </span>
          <span className="text-[13px] font-semibold text-foreground-800">Upload a picture of your signature</span>
          <span className="text-[12px] text-foreground-500">PNG, JPEG or WebP, up to 5 MB</span>
        </label>
      )}
      <div className="flex items-center gap-4">
        {uploaded && (
          <>
            <button type="button" onClick={() => inputRef.current?.click()} className="inline-flex items-center gap-1.5 text-[12px] font-medium text-primary-600 hover:underline">
              <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />Replace
            </button>
            <button type="button" onClick={onRemove} className="inline-flex items-center gap-1.5 text-[12px] font-medium text-red-600 hover:underline">
              <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />Remove
            </button>
          </>
        )}
        {!uploaded && (
          <p id="fl-signature-upload-hint" className="text-[12px] text-foreground-500">
            Sign on white paper and take a clear, well-lit photo.
          </p>
        )}
      </div>
    </div>
  );
}

function SignatureStep({
  signatoryName, mode, onMode, strokes, onDraw, typed, chosen, onChoose, uploaded, onUpload, onRemoveUpload,
  error, submitting, onBack, onFinish,
}: {
  signatoryName: string;
  mode: SignMode;
  onMode: (mode: SignMode) => void;
  strokes: PadData;
  onDraw: (dataUrl: string, strokes: PadData) => void;
  typed: string;
  chosen: boolean;
  onChoose: (selected: boolean) => void;
  uploaded: string;
  onUpload: (file: File) => void;
  onRemoveUpload: () => void;
  error?: string;
  submitting: boolean;
  onBack: () => void;
  onFinish: () => void;
}) {
  const modes: { value: SignMode; label: string; icon: ReactNode }[] = [
    { value: 'draw', label: 'Draw', icon: <PenLine aria-hidden="true" className="h-4 w-4" /> },
    { value: 'choose', label: 'Choose', icon: <Type aria-hidden="true" className="h-4 w-4" /> },
    { value: 'upload', label: 'Upload', icon: <ImageUp aria-hidden="true" className="h-4 w-4" /> },
  ];

  return (
    <section aria-labelledby="fl-signature-heading" className="overflow-hidden rounded-2xl border border-foreground-200/70 bg-background-50 shadow-sm">
      <div className="space-y-5 p-5 sm:p-6">
        <SectionHeading
          id="fl-signature-heading"
          icon={<ShieldCheck aria-hidden="true" className="h-5 w-5" />}
          title="Electronic signature declaration agreement"
          text="Read the declaration, then add your signature."
        />
        <div className="space-y-2.5 rounded-xl border border-foreground-200/60 bg-background-100/60 p-4 text-[13px] leading-relaxed text-foreground-700">
          <p>
            Your usage of this platform is subject to our terms and conditions and privacy policy. It may also be
            subject to conditions from the organisation that has provided you with access to this platform. We enable
            you to confirm agreement through use of an electronic signature.
          </p>
          <p>Your electronic signature can only be applied by you when you are logged into this platform.</p>
          <p>
            Please draw your signature with your mouse or touch-screen, choose one made from your name, or upload a
            picture of it, then click ‘Finish’.
          </p>
        </div>

        <div>
          <p className="mb-2 text-[13px] font-semibold text-foreground-800">
            Signature<RequiredMark />
          </p>
          <div role="tablist" aria-label="How to sign" className="inline-flex rounded-xl bg-background-200/70 p-1">
            {modes.map((m) => (
              <button
                key={m.value}
                type="button"
                role="tab"
                id={`fl-tab-${m.value}`}
                aria-selected={mode === m.value}
                aria-controls={`fl-panel-${m.value}`}
                onClick={() => onMode(m.value)}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-[13px] font-semibold transition-smooth ${
                  mode === m.value ? 'bg-white text-primary-700 shadow-sm' : 'text-foreground-600 hover:text-foreground-900'
                }`}
              >
                {m.icon}{m.label}
              </button>
            ))}
          </div>
        </div>

        <div role="tabpanel" id={`fl-panel-${mode}`} aria-labelledby={`fl-tab-${mode}`}>
          {mode === 'draw' && <DrawPad strokes={strokes} onChange={onDraw} invalid={!!error} />}
          {mode === 'choose' && (
            typed ? (
              <div className="space-y-2">
                <button
                  type="button"
                  aria-pressed={chosen}
                  onClick={() => onChoose(!chosen)}
                  className={`relative flex h-40 w-full max-w-md items-center justify-center rounded-xl border-2 bg-white p-4 transition-smooth ${
                    chosen ? 'border-primary-500 ring-4 ring-primary-100' : `border-dashed hover:border-primary-400 ${error ? 'border-red-300' : 'border-foreground-200'}`
                  }`}
                >
                  {chosen && (
                    <span className="absolute right-3 top-3 flex h-6 w-6 items-center justify-center rounded-full bg-primary-600 text-white">
                      <Check aria-hidden="true" className="h-3.5 w-3.5" />
                    </span>
                  )}
                  <img src={typed} alt={`Signature of ${signatoryName}`} className="max-h-full max-w-full object-contain" />
                </button>
                <p className="text-[12px] text-foreground-500">
                  {chosen ? 'Selected — this is the signature that will be used.' : 'Click the signature to use it.'}
                </p>
              </div>
            ) : (
              <p className="text-[13px] text-foreground-500">
                {signatoryName ? 'Preparing your signature…' : 'No name is on record for you, so please draw or upload your signature instead.'}
              </p>
            )
          )}
          {mode === 'upload' && <UploadPanel uploaded={uploaded} onUpload={onUpload} onRemove={onRemoveUpload} invalid={!!error} />}
          <FieldError id="fl-signature-error" message={error} />
        </div>
      </div>
      <div className="flex flex-col-reverse gap-2 border-t border-foreground-200/60 bg-background-50 px-5 py-4 sm:flex-row sm:justify-between">
        <button type="button" className={`${btnSecondary} justify-center`} onClick={onBack} disabled={submitting}>
          <ArrowLeft aria-hidden="true" className="h-4 w-4" /> Back
        </button>
        <button type="button" className={`${btnPrimary} justify-center`} onClick={onFinish} disabled={submitting}>
          {submitting ? 'Saving…' : 'Finish'} <Check aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
    </section>
  );
}

// ---- page -------------------------------------------------------------------

const EMPTY_VALUES: FirstLoginDetailsValues = {
  title: '', dateOfBirth: '', phone: '', country: DEFAULT_COUNTRY, postcode: '',
  addressLine1: '', addressLine2: '', townCity: '', county: '',
};

export default function LearnerWelcomePage() {
  const { auth } = useAuth();
  const account = auth.account;
  const navigate = useNavigate();
  const learnerId = account?.role === 'learner' && account.learnerType !== 'commercial' ? String(account.subjectId) : '';

  const [state, setState] = useState<FirstLoginDetailsState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [step, setStep] = useState<1 | 2>(1);
  const [values, setValues] = useState<FirstLoginDetailsValues>(EMPTY_VALUES);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [mode, setMode] = useState<SignMode>('draw');
  const [strokes, setStrokes] = useState<PadData>([]);
  const [drawn, setDrawn] = useState('');
  const [typed, setTyped] = useState('');
  const [chosen, setChosen] = useState(false);
  const [uploaded, setUploaded] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!learnerId) return undefined;
    let cancelled = false;
    setLoadError(null);
    fetchFirstLoginDetails(learnerId)
      .then((data) => {
        if (cancelled) return;
        if (!data.required) {
          // Already done, or not an account these screens are for.
          navigate(isOnboardingStatus(data.programmeStatus) ? ONBOARDING_ROUTE : '/learner/home', { replace: true });
          return;
        }
        setState(data);
        setValues({ ...EMPTY_VALUES, ...data.details, country: data.details.country || DEFAULT_COUNTRY });
      })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); });
    return () => { cancelled = true; };
  }, [learnerId, reloadToken, navigate]);

  const signatoryName = state?.signatoryName || '';
  useEffect(() => {
    if (!signatoryName) return undefined;
    let cancelled = false;
    createTypedSignature(signatoryName).then((url) => { if (!cancelled) setTyped(url); });
    return () => { cancelled = true; };
  }, [signatoryName]);

  if (!learnerId) return <Navigate to="/learner/home" replace />;

  const clearSignatureError = () => setErrors((e) => (e.signature ? { ...e, signature: undefined } : e));

  const change = (key: keyof FirstLoginDetailsValues, value: string) => {
    setValues((v) => ({ ...v, [key]: value }));
    setErrors((e) => (e[key] ? { ...e, [key]: undefined } : e));
  };

  const next = () => {
    const found = validateDetails(values);
    setErrors(found);
    const first = DETAIL_FIELDS.find((k) => found[k]);
    if (first) {
      document.getElementById(`fl-${first}`)?.focus();
      return;
    }
    setStep(2);
    window.scrollTo({ top: 0 });
  };

  const upload = (file: File) => {
    uploadedSignatureToPng(file)
      .then((png) => { setUploaded(png); clearSignatureError(); })
      .catch((e: Error) => setErrors((prev) => ({ ...prev, signature: e.message })));
  };

  const signature = mode === 'draw' ? drawn : mode === 'choose' ? (chosen ? typed : '') : uploaded;

  const finish = async () => {
    if (!state) return;
    const found = validateDetails(values);
    if (Object.keys(found).length) {
      setErrors(found);
      setStep(1);
      return;
    }
    if (!signature) {
      setErrors({ signature: 'Your signature is required.' });
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const saved = await submitFirstLoginDetails(learnerId, values, signature, state.csrfToken);
      // Hand the new status to the sidebar straight away; it caches per session.
      syncLearnerStatus('apprenticeship', learnerId, saved.programmeStatus);
      navigate(ONBOARDING_ROUTE, { replace: true });
    } catch (e) {
      if (e instanceof FirstLoginDetailsError && Object.keys(e.fields).length) {
        setErrors(e.fields as FieldErrors);
        if (DETAIL_FIELDS.some((k) => e.fields[k])) setStep(1);
      } else {
        setSubmitError(e instanceof Error ? e.message : 'Your details could not be saved. Please try again.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <WorkspaceShell
      role="learner"
      roleLabel={learnerNav.label}
      // Nothing else is reachable until these are done, so no menu is offered.
      navItems={[]}
      workspaceLabel={learnerNav.workspaceLabel}
      pageTitle="Welcome"
      pageSubtitle={step === 1 ? 'Step 1 of 2 — your details' : 'Step 2 of 2 — your signature'}
      userName={signatoryName || account?.displayName || 'Learner'}
      userRole="Learner"
      showBackButton={false}
      hideBreadcrumbs
    >
      <main className="page-container w-full min-w-0 p-3 md:p-6">
        {/* Held to a readable width: stretched across a wide monitor the two
            columns drift apart and the signature box becomes a letterbox. */}
        <div className="mx-auto w-full max-w-5xl space-y-4">
          <div className="flex flex-col gap-4 rounded-2xl border border-foreground-200/70 bg-gradient-to-br from-primary-50/80 to-background-50 p-5 shadow-sm sm:flex-row sm:items-center sm:justify-between sm:p-6">
            <div>
              <h1 className="font-heading text-xl font-bold text-foreground-950">
                Welcome{signatoryName ? `, ${signatoryName.split(/\s+/)[0]}` : ''}
              </h1>
              <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-foreground-600">
                Before you start your enrolment, tell us a little about yourself and add your electronic signature.
                It takes about two minutes.
              </p>
            </div>
            <Stepper step={step} />
          </div>

          {!state && !loadError && (
            <div className="rounded-2xl border border-foreground-200/60 bg-background-50 p-5"><RowsSkeleton rows={5} /></div>
          )}
          {loadError && (
            <div className="rounded-2xl border border-foreground-200/60 bg-background-50 py-16 text-center text-[13px]">
              <p className="mb-3 inline-flex items-center gap-1.5 text-red-600"><AlertCircle aria-hidden="true" className="h-4 w-4" />{loadError}</p>
              <div>
                <button className={btnSecondary} onClick={() => setReloadToken((n) => n + 1)}>
                  <RefreshCw aria-hidden="true" className="h-4 w-4" />Retry
                </button>
              </div>
            </div>
          )}
          {state && submitError && (
            <p role="alert" className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">
              <AlertCircle aria-hidden="true" className="h-4 w-4 shrink-0" />{submitError}
            </p>
          )}
          {state && step === 1 && <DetailsStep values={values} errors={errors} onChange={change} onNext={next} />}
          {state && step === 2 && (
            <SignatureStep
              signatoryName={signatoryName}
              mode={mode}
              onMode={(m) => { setMode(m); clearSignatureError(); }}
              strokes={strokes}
              onDraw={(url, data) => { setDrawn(url); setStrokes(data); if (url) clearSignatureError(); }}
              typed={typed}
              chosen={chosen}
              onChoose={(selected) => { setChosen(selected); if (selected) clearSignatureError(); }}
              uploaded={uploaded}
              onUpload={upload}
              onRemoveUpload={() => setUploaded('')}
              error={errors.signature}
              submitting={submitting}
              onBack={() => { setStep(1); window.scrollTo({ top: 0 }); }}
              onFinish={finish}
            />
          )}
        </div>
      </main>
    </WorkspaceShell>
  );
}
