import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, ClipboardCheck, ClipboardList, Loader2, MapPin, RotateCcw, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  ApplicationSubmitError,
  compactPostcode,
  fetchEligibilityForm,
  isUkPostcode,
  lookupPostcode,
  submitApprenticeshipApplication,
  type ApplicationOutcome,
  type PostcodeCheck,
} from '@/api/apprenticeshipApplication';
import {
  answerFor,
  businessToday,
  formPage,
  formSections,
  isPostcodeQuestion,
  isQuestion,
  isShown,
  toPayload,
  validateSection,
  type AnswerValue,
  type Answers,
  type Errors,
  type Question,
} from './form';
import { BUILTIN_ITEMS, isBuiltinQuestion } from './layout/registry';
import { resolveLayout } from './layout/resolve';
import type { EligibilityLayout } from './layout/types';
// The LMS public-page frame (logo bar, banner, footer), shared with the coach
// booking and LMS introduction pages; application.module.css styles the form
// and the dark-theme version of that frame.
import frame from '../coach-booking/booking.module.css';
import styles from './application.module.css';

/** A postcode check as the page tracks it, by compact postcode. */
type Check = PostcodeCheck | { status: 'checking' };

interface LoadedForm {
  layout: EligibilityLayout;
  /** The published version it was built from; null for the standard form. */
  version: number | null;
}

const RETRY_LATER = 'We could not check your postcodes just now, so we could not confirm your eligibility. '
  + 'Your answers have been kept: please try again in a few minutes.';

/** The questions whose answer is looked up as a UK postcode. */
const POSTCODE_KEYS = Object.values(BUILTIN_ITEMS).filter((item) => isBuiltinQuestion(item) && item.postcode).map((item) => item.key);

/**
 * The public apprenticeship eligibility form at /apprenticeship_application.
 *
 * No sign-in: the applicant has no account. The questions are the layout
 * published from the "Edit eligibility form" builder (the standard form until
 * one is), in sections each checked before moving on; nothing reaches the
 * server until "Submit application", which saves one row to
 * enrolment.eligibility_form. The form collects answers only -- staff assess
 * eligibility, so no funding rule is applied here.
 *
 * Postcodes are looked up as they are typed, to show where one in England is.
 * Nothing under the question says a postcode is wrong and nothing stops the
 * applicant: the server saves the application either way, and when a postcode
 * is not a UK postcode, cannot be found or is outside England the applicant is
 * told they are not eligible, with the reasons. An eligible applicant is
 * emailed a link to create their password (or told to sign in when the address
 * already has an account); when the postcodes could not be checked the form
 * stays open so they can submit again.
 */
export default function ApprenticeshipApplicationPage() {
  const [form, setForm] = useState<LoadedForm | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loadToken, setLoadToken] = useState(0);
  const [answers, setAnswers] = useState<Answers>({});
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [outcome, setOutcome] = useState<ApplicationOutcome | null>(null);
  const submitted = outcome !== null;
  const [dirty, setDirty] = useState(false);
  const [focusErrors, setFocusErrors] = useState(0);
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const inFlight = useRef(new Map<string, Promise<PostcodeCheck>>());
  const answersRef = useRef(answers);
  answersRef.current = answers;
  const checksRef = useRef(checks);
  checksRef.current = checks;
  const formRef = useRef<HTMLFormElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const successRef = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const today = businessToday();

  useEffect(() => {
    let cancelled = false;
    setLoadError('');
    fetchEligibilityForm()
      .then((res) => {
        if (!cancelled) setForm({ layout: resolveLayout(res.layout), version: res.version });
      })
      .catch((reason: Error) => {
        if (!cancelled) setLoadError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [loadToken]);

  const sections = useMemo(() => (form ? formSections(form.layout) : []), [form]);
  const page = useMemo(() => (form ? formPage(form.layout) : null), [form]);

  // Nothing is saved before Submit, so leaving part-way would lose the answers.
  useEffect(() => {
    if (!dirty || submitted) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty, submitted]);

  // A new section starts at its heading, so keyboard and screen-reader users begin at the top.
  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    formRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
    headingRef.current?.focus({ preventScroll: true });
  }, [step, form]);

  useEffect(() => {
    if (!focusErrors) return;
    const invalid = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]');
    const target = invalid?.matches('input, select') ? invalid : invalid?.querySelector<HTMLElement>('input');
    target?.focus();
  }, [focusErrors]);

  useEffect(() => {
    if (submitted) successRef.current?.focus();
  }, [submitted]);

  /**
   * Look a postcode up once; checks of the same postcode share the answer. A
   * check that could not be made is tried again next time.
   */
  const checkPostcode = useCallback((value: string): Promise<PostcodeCheck> | null => {
    if (!isUkPostcode(value)) return null;
    const code = compactPostcode(value);
    const known = checksRef.current[code];
    if (known && known.status !== 'checking' && known.status !== 'unavailable') return Promise.resolve(known);
    let pending = inFlight.current.get(code);
    if (!pending) {
      setChecks((current) => ({ ...current, [code]: { status: 'checking' } }));
      pending = lookupPostcode(value).then((result) => {
        inFlight.current.delete(code);
        setChecks((current) => ({ ...current, [code]: result }));
        return result;
      });
      inFlight.current.set(code, pending);
    }
    return pending;
  }, []);

  // Check a postcode once the applicant pauses typing it.
  const postcodeValues = POSTCODE_KEYS.map((key) => String(answers[key] ?? '')).join('\n');
  useEffect(() => {
    const timer = window.setTimeout(() => {
      for (const value of postcodeValues.split('\n')) void checkPostcode(value);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [postcodeValues, checkPostcode]);

  const goTo = (index: number) => {
    moved.current = true;
    setSubmitError('');
    setStep(index);
  };

  const setAnswer = (key: string, value: AnswerValue) => {
    setAnswers((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setErrors((current) => {
      if (!current[key]) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  };

  if (!form || !page || sections.length === 0) {
    return (
      <Frame>
        <section className={styles.card} aria-live="polite">
          {loadError || (form && sections.length === 0) ? (
            <div className={styles.loading}>
              <p className={styles.alert} role="alert">{loadError || 'The application form is not available just now.'}</p>
              <button type="button" className={styles.secondary} onClick={() => setLoadToken((n) => n + 1)}>
                <RotateCcw aria-hidden="true" /> Try again
              </button>
            </div>
          ) : (
            <p className={styles.loading}><Loader2 className={styles.spin} aria-hidden="true" /> Loading the application form…</p>
          )}
        </section>
      </Frame>
    );
  }

  const index = Math.min(step, sections.length - 1);
  const section = sections[index];
  const last = index === sections.length - 1;
  const percent = Math.round(((index + 1) / sections.length) * 100);
  const SectionIcon = section.icon;

  const submit = async () => {
    setSubmitting(true);
    setSubmitError('');
    try {
      const result = await submitApprenticeshipApplication(toPayload(answersRef.current, sections, form.version));
      // The postcodes could not be checked, so eligibility was not decided:
      // keep the form and its answers so the applicant can submit again.
      if (result.outcome === 'retryLater') setSubmitError(RETRY_LATER);
      else setOutcome(result);
    } catch (reason) {
      const failure = reason instanceof ApplicationSubmitError
        ? reason
        : new ApplicationSubmitError('We could not submit your application. Please try again.', 0);
      if (failure.formChanged) {
        // Republished while the applicant was filling it in: show the form in
        // force from the start. Answers are kept by question, so nothing
        // already answered has to be typed again.
        let message = failure.message;
        try {
          const fresh = await fetchEligibilityForm();
          setForm({ layout: resolveLayout(fresh.layout), version: fresh.version });
        } catch {
          message = `${failure.message} We could not load the updated form just now: check your connection, then submit again.`;
        }
        setErrors({});
        goTo(0);
        setSubmitError(message);
        return;
      }
      // The server refused specific answers: show them where they were asked.
      const refused = sections.findIndex(candidate => candidate.items.some(item => isQuestion(item) && failure.fields[item.key]));
      if (refused >= 0) {
        setErrors(failure.fields);
        if (refused !== index) goTo(refused);
        else setFocusErrors(count => count + 1);
      }
      setSubmitError(failure.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleContinue = (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    const found = validateSection(section, answers, today);
    setErrors(found);
    if (Object.keys(found).length) {
      setFocusErrors(count => count + 1);
      return;
    }
    if (last) void submit();
    else goTo(index + 1);
  };

  let number = 0;
  const programme = typeof answers.programme === 'string' ? answers.programme : '';
  const email = String(answers.workEmail ?? '').trim();

  return (
    <Frame hero={page}>
      {outcome?.eligible === false ? (
        <section className={styles.card} aria-labelledby="application-submitted">
          <div className={styles.success}>
            <span className={`${styles.successIcon} ${styles.notEligibleIcon}`} aria-hidden="true"><XCircle /></span>
            <h2 id="application-submitted" ref={successRef} tabIndex={-1}>You are not eligible</h2>
            <p>
              Thank you, {String(answers.firstName ?? '').trim()}. We have received your application
              {programme ? <> for <strong>{programme}</strong></> : null}, but you are not eligible for an
              apprenticeship with us: apprenticeship funding is only available in England.
            </p>
            {outcome.reasons.length > 0 && (
              <ul className={styles.reasons}>
                {outcome.reasons.map(reason => <li key={reason}>{reason}</li>)}
              </ul>
            )}
            <p>If you think this is a mistake, please get in touch with our team. You can now close this page.</p>
          </div>
        </section>
      ) : outcome?.outcome === 'existingAccount' ? (
        <section className={styles.card} aria-labelledby="application-submitted">
          <div className={styles.success}>
            <span className={styles.successIcon} aria-hidden="true"><CheckCircle2 /></span>
            <h2 id="application-submitted" ref={successRef} tabIndex={-1}>You already have an account</h2>
            <p>
              Thank you, {String(answers.firstName ?? '').trim()}. We have received your application
              {programme ? <> for <strong>{programme}</strong></> : null}. An account already exists
              for <strong>{email}</strong>.
            </p>
            <p>
              Please <Link to="/login">sign in</Link>. If you have not set a password yet, or have forgotten it,
              use <Link to="/forgot-password">Forgot password</Link> and we will email you a link.
            </p>
          </div>
        </section>
      ) : outcome?.outcome === 'invited' || outcome?.outcome === 'invitePending' ? (
        <section className={styles.card} aria-labelledby="application-submitted">
          <div className={styles.success}>
            <span className={styles.successIcon} aria-hidden="true"><CheckCircle2 /></span>
            <h2 id="application-submitted" ref={successRef} tabIndex={-1}>You are eligible</h2>
            <p>
              Thank you, {String(answers.firstName ?? '').trim()}. We have received your application
              {programme ? <> for <strong>{programme}</strong></> : null}, and you are eligible for an
              apprenticeship with us.
            </p>
            {outcome.outcome === 'invited' ? (
              <p>
                We have sent an email to <strong>{email}</strong> with a link to create your password. The link
                expires in 7 days. If you cannot find the email, check your junk or spam folder.
              </p>
            ) : (
              <p>
                We could not send your invitation email just now. Our team will send you a link to create your
                password at <strong>{email}</strong>.
              </p>
            )}
            <p>You can now close this page.</p>
          </div>
        </section>
      ) : submitted ? (
        <section className={styles.card} aria-labelledby="application-submitted">
          <div className={styles.success}>
            <span className={styles.successIcon} aria-hidden="true"><CheckCircle2 /></span>
            <h2 id="application-submitted" ref={successRef} tabIndex={-1}>Application submitted</h2>
            <p>
              Thank you, {String(answers.firstName ?? '').trim()}. We have received your application
              {programme ? <> for <strong>{programme}</strong></> : null}.
            </p>
            <p>You can now close this page.</p>
          </div>
        </section>
      ) : (
        <form ref={formRef} className={styles.card} onSubmit={handleContinue} noValidate aria-labelledby="application-section-title">
          <div className={styles.progressRow}>
            <span className={styles.sectionCount}>Section {index + 1} of {sections.length}</span>
            <div
              className={styles.track}
              role="progressbar"
              aria-label="Application progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <span style={{ width: `${percent}%` }} />
            </div>
            <span className={styles.percent}>{percent}% complete</span>
          </div>

          <div className={styles.sectionHead}>
            <span className={styles.sectionIcon} aria-hidden="true"><SectionIcon /></span>
            <div>
              <h2 id="application-section-title" ref={headingRef} tabIndex={-1}>{section.title}</h2>
              {section.description && <p>{section.description}</p>}
            </div>
          </div>

          <div className={styles.questions}>
            {section.items.map(item => {
              if (!isQuestion(item)) return <p key={item.key} className={styles.note}>{item.text}</p>;
              if (!isShown(item, answers)) return null;
              number += 1;
              const value = answerFor(answers, item);
              const postcode = isPostcodeQuestion(item);
              return (
                <QuestionRow
                  key={item.key}
                  question={item}
                  number={number}
                  value={value}
                  error={errors[item.key]}
                  today={today}
                  onChange={next => setAnswer(item.key, next)}
                  onBlur={postcode ? () => void checkPostcode(String(value ?? '')) : undefined}
                  check={postcode && isUkPostcode(String(value ?? '')) ? checks[compactPostcode(String(value))] : undefined}
                />
              );
            })}
          </div>

          {submitError && <p className={styles.alert} role="alert">{submitError}</p>}

          <div className={styles.actions}>
            {index > 0 && (
              <button type="button" className={styles.secondary} onClick={() => goTo(index - 1)} disabled={submitting}>
                <ArrowLeft aria-hidden="true" /> Previous
              </button>
            )}
            <button type="submit" className={styles.primary} disabled={submitting}>
              {last ? (submitting ? 'Submitting…' : 'Submit application') : 'Continue'}
              <ArrowRight aria-hidden="true" />
            </button>
          </div>
        </form>
      )}
    </Frame>
  );
}

/** The public-page frame: logo bar, the purple banner (once the form's wording is known) and footer. */
function Frame({ hero, children }: { hero?: { eyebrow: string; title: string; intro: string }; children: ReactNode }) {
  return (
    <main className={`${frame.page} ${styles.page}`}>
      <div className={frame.container}>
        <div className={`${frame.brandBar} ${styles.brandBar}`}>
          <img src="/assets/kbc-logo.png" alt="Kent Business College" width="175" height="80" />
          <span><ClipboardList size={17} aria-hidden="true" />Apprenticeship application</span>
        </div>
        {hero && (
          <header className={frame.hero}>
            <div className={frame.heroCopy}>
              <p className={frame.eyebrow}>{hero.eyebrow}</p>
              <h1>{hero.title}</h1>
              <p>{hero.intro}</p>
              <span className={frame.accessNote}><CheckCircle2 size={16} aria-hidden="true" />No LMS account needed to apply</span>
            </div>
            <div className={frame.heroIcon} aria-hidden="true"><ClipboardCheck strokeWidth={1.3} /></div>
          </header>
        )}
        {children}
        <footer className={`${frame.footer} ${styles.footer}`}>
          <span>Kent Business College</span>
          <span>Learning support, one conversation at a time.</span>
        </footer>
      </div>
    </main>
  );
}

interface QuestionRowProps {
  question: Question;
  number: number;
  value: AnswerValue;
  error?: string;
  today: string;
  onChange: (value: string | boolean) => void;
  onBlur?: () => void;
  /** Postcode questions: the lookup of the current answer. */
  check?: Check;
}

function QuestionRow({ question, number, value, error, today, onChange, onBlur, check }: QuestionRowProps) {
  const id = `application-${question.key}`;
  const labelId = `${id}-label`;
  const hintId = question.hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const locationId = isPostcodeQuestion(question) ? `${id}-location` : undefined;
  const describedBy = [hintId, errorId, locationId].filter(Boolean).join(' ') || undefined;
  const invalid = Boolean(error);

  const prompt = (
    <>
      <span className={styles.number}>{number}.</span>{' '}
      {question.label}
      {question.required && <span className={styles.required} aria-hidden="true"> *</span>}
    </>
  );
  const hint = question.hint && <span id={hintId} className={styles.hint}>{question.hint}</span>;
  const message = error && <p id={errorId} className={styles.error}>{error}</p>;

  if (question.kind === 'choice' || question.kind === 'yesno') {
    const options = question.kind === 'yesno'
      ? [{ label: 'Yes', value: true }, { label: 'No', value: false }]
      : question.options.map(option => ({ label: option, value: option }));
    return (
      <div className={styles.row}>
        <div className={styles.prompt}>
          <span id={labelId} className={styles.label}>{prompt}</span>
          {hint}
        </div>
        <div className={styles.control}>
          <div
            role="radiogroup"
            aria-labelledby={labelId}
            aria-describedby={describedBy}
            aria-required={question.required}
            aria-invalid={invalid}
            className={`${styles.options} ${invalid ? styles.optionsInvalid : ''}`}
          >
            {options.map(option => {
              const selected = value === option.value;
              return (
                <label key={option.label} className={`${styles.option} ${selected ? styles.optionSelected : ''}`}>
                  <input type="radio" name={question.key} checked={selected} onChange={() => onChange(option.value)} />
                  <span>{option.label}</span>
                </label>
              );
            })}
          </div>
          {message}
        </div>
      </div>
    );
  }

  const shared = {
    id,
    'aria-describedby': describedBy,
    'aria-invalid': invalid,
    'aria-required': question.required,
  };

  return (
    <div className={styles.row}>
      <div className={styles.prompt}>
        <label htmlFor={id} className={styles.label}>{prompt}</label>
        {hint}
      </div>
      <div className={styles.control}>
        {question.kind === 'select' ? (
          <select
            {...shared}
            className={`${styles.input} ${styles.select} ${invalid ? styles.invalid : ''}`}
            value={String(value ?? '')}
            onChange={event => onChange(event.target.value)}
          >
            <option value="">Please select</option>
            {question.options.map(option => <option key={option} value={option}>{option}</option>)}
          </select>
        ) : (
          <input
            {...shared}
            className={`${styles.input} ${invalid ? styles.invalid : ''}`}
            type={question.kind}
            value={String(value ?? '')}
            onChange={event => onChange(event.target.value)}
            onBlur={onBlur}
            maxLength={'maxLength' in question ? question.maxLength : undefined}
            max={question.kind === 'date' && !question.allowFuture ? today : undefined}
            min={question.kind === 'date' ? '1900-01-01' : undefined}
            step={question.kind === 'number' ? 'any' : undefined}
            inputMode={question.kind === 'number' ? 'decimal' : undefined}
            autoComplete={'autoComplete' in question ? question.autoComplete : undefined}
          />
        )}
        {message}
        {locationId && (
          <div id={locationId} aria-live="polite">
            <PostcodeLocation check={check} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Where the postcode is, under the postcode question -- shown only for one
 * found in England. Nothing is said about any other postcode here: whether it
 * makes the applicant ineligible is decided, and told, on submit.
 */
function PostcodeLocation({ check }: { check?: Check }) {
  if (!check) return null;
  if (check.status === 'checking') {
    return <p className={styles.location}><Loader2 className={styles.spin} aria-hidden="true" /> Checking postcode…</p>;
  }
  if (check.status !== 'found' || !check.inEngland) return null;
  const place = [check.district, check.region, check.country].filter(Boolean).join(', ');
  return <p className={`${styles.location} ${styles.locationFound}`}><MapPin aria-hidden="true" /> {place}</p>;
}
