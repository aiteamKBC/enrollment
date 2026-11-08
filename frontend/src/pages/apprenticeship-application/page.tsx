import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, ClipboardCheck, Info, Loader2, MapPin, RotateCcw, XCircle } from 'lucide-react';
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
  dateNotice,
  formPage,
  formSections,
  EMPLOYMENT_RESTRICTION_ERROR,
  EMPLOYER_POSTCODE_UNAVAILABLE,
  WORKPLACE_POSTCODE_UNAVAILABLE,
  INVALID_POSTCODE,
  UNKNOWN_POSTCODE,
  isEmploymentRestricted,
  isNoteShown,
  isPostcodeQuestion,
  isQuestion,
  isRequired,
  isShown,
  minimumDate,
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

const RETRY_LATER = 'We could not complete your submission just now. '
  + 'Your answers have been kept: please try again in a few minutes.';
const EMPLOYMENT_REQUIREMENTS_NOTICE_KEY = 'note.employmentRequirements';
const EMPLOYMENT_CONTACT_EMAIL = 'office@kentbusinesscollege.org';

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
 * Postcodes are looked up as they are typed. Employer Postcode must also be
 * verified before continuing; final submission independently verifies it.
 * Country-based eligibility is decided on submission. An eligible applicant is
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
  const [verifyingEmployer, setVerifyingEmployer] = useState(false);
  const pendingContinue = useRef<object | null>(null);
  const [submitError, setSubmitError] = useState('');
  const [outcome, setOutcome] = useState<ApplicationOutcome | null>(null);
  const [historicalMessage, setHistoricalMessage] = useState('');
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
        if (!cancelled) setForm({ layout: resolveLayout(res.layout, res.workplaceEnabled === true), version: res.version });
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
  const employmentStep = sections.findIndex(candidate => candidate.items.some(item => isQuestion(item) && item.key === 'employmentStatus'));

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
      checksRef.current = { ...checksRef.current, [code]: { status: 'checking' } };
      setChecks(checksRef.current);
      pending = lookupPostcode(value).then((result) => {
        inFlight.current.delete(code);
        checksRef.current = { ...checksRef.current, [code]: result };
        setChecks(checksRef.current);
        return result;
      });
      inFlight.current.set(code, pending);
    }
    return pending;
  }, []);

  // Check a postcode once the applicant pauses typing it.
  const residentialUk = ['', 'United Kingdom'].includes(String(answers.countryOfResidence ?? ''));
  const postcodeValues = POSTCODE_KEYS.filter(key => key !== 'postcode' || residentialUk).map((key) => String(answers[key] ?? '')).join('\n');
  useEffect(() => {
    const timer = window.setTimeout(() => {
      for (const value of postcodeValues.split('\n')) void checkPostcode(value);
    }, 600);
    return () => window.clearTimeout(timer);
  }, [postcodeValues, checkPostcode]);

  const goTo = (index: number) => {
    if (index > step && index > employmentStep && isEmploymentRestricted(answersRef.current.employmentStatus)) return;
    pendingContinue.current = null;
    setVerifyingEmployer(false);
    moved.current = true;
    setSubmitError('');
    setStep(index);
  };

  const setAnswer = (key: string, value: AnswerValue) => {
    // An outstanding check must never advance using answers that have changed.
    pendingContinue.current = null;
    setVerifyingEmployer(false);
    if (['employerPostcode', 'mainWorkplacePostcode', 'workplaceArrangement', 'majorityHoursInEngland'].includes(key)) setSubmitError('');
    setAnswers((current) => ({ ...current, [key]: value,
      ...(key === 'workplaceArrangement' || (key === 'employmentStatus' && value !== 'In paid employment') ? { mainWorkplacePostcode: '' } : {}) }));
    setDirty(true);
    setErrors((current) => {
      const next = { ...current };
      delete next[key];
      if (key === 'countryOfResidence') delete next.postcode;
      if (key === 'workplaceArrangement' || key === 'employmentStatus') delete next.mainWorkplacePostcode;
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
  const employmentRestricted = index >= employmentStep && isEmploymentRestricted(answers.employmentStatus);
  const percent = Math.round(((index + 1) / sections.length) * 100);
  const SectionIcon = section.icon;

  const submit = async () => {
    if (isEmploymentRestricted(answersRef.current.employmentStatus)) {
      setErrors(current => ({ ...current, employmentStatus: EMPLOYMENT_RESTRICTION_ERROR }));
      setSubmitError(EMPLOYMENT_RESTRICTION_ERROR);
      return;
    }
    setSubmitting(true);
    setSubmitError('');
    setHistoricalMessage('');
    try {
      const result = await submitApprenticeshipApplication(toPayload(answersRef.current, sections, form.version));
      // The postcodes could not be checked, so eligibility was not decided:
      // keep the form and its answers so the applicant can submit again.
      if (result.review || result.outcome === 'pendingAssessment') {
        // Historical receipts stay intact, but are not a third new screening result.
        setHistoricalMessage(result.review?.message || 'Your previously submitted application is awaiting assessment. No new eligibility decision or invitation has been issued.');
      } else if (result.outcome === 'retryLater' || typeof result.eligible !== 'boolean') {
        setSubmitError(RETRY_LATER);
      } else setOutcome(result);
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
          setForm({ layout: resolveLayout(fresh.layout, fresh.workplaceEnabled === true), version: fresh.version });
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

  const handleContinue = async (event?: FormEvent) => {
    event?.preventDefault();
    if (submitting || pendingContinue.current) return;
    const found = validateSection(section, answers, today, sections);
    setErrors(found);
    if (employmentRestricted) {
      setErrors({ ...found, employmentStatus: EMPLOYMENT_RESTRICTION_ERROR });
      setFocusErrors(count => count + 1);
      return;
    }
    if (Object.keys(found).length) {
      setFocusErrors(count => count + 1);
      return;
    }
    const postcodes = section.items.filter(item => isQuestion(item) && ['employerPostcode', 'mainWorkplacePostcode'].includes(item.key) && isShown(item, answers));
    for (const postcode of postcodes) {
      const value = String(answers[postcode.key] ?? '');
      if (!value.trim()) continue;
      if (!isUkPostcode(value)) {
        setErrors({ [postcode.key]: INVALID_POSTCODE });
        setFocusErrors(count => count + 1);
        return;
      }
      const request = {};
      pendingContinue.current = request;
      setVerifyingEmployer(true);
      try {
        const result = await checkPostcode(value);
        if (pendingContinue.current !== request) return;
        if (result?.status !== 'found') {
          const unavailable = postcode.key === 'mainWorkplacePostcode' ? WORKPLACE_POSTCODE_UNAVAILABLE : EMPLOYER_POSTCODE_UNAVAILABLE;
          setErrors({ [postcode.key]: result?.status === 'notFound' ? UNKNOWN_POSTCODE : unavailable });
          setFocusErrors(count => count + 1);
          return;
        }
      } finally {
        if (pendingContinue.current === request) {
          pendingContinue.current = null;
          setVerifyingEmployer(false);
        }
      }
    }
    if (last) void submit();
    else goTo(index + 1);
  };

  let number = 0;
  const email = String(answers.workEmail ?? '').trim();

  return (
    <Frame hero={page}>
      {submitted ? (
        <section className={styles.card} aria-labelledby="application-submitted">
          <div className={styles.success}>
            <span className={`${styles.successIcon} ${outcome.eligible ? '' : styles.notEligibleIcon}`} aria-hidden="true">
              {outcome.eligible ? <CheckCircle2 /> : <XCircle />}
            </span>
            <h2 id="application-submitted" ref={successRef} tabIndex={-1}>
              {outcome.eligible ? 'You Are Eligible to Proceed!' : 'You Are Not Currently Eligible'}
            </h2>
            {outcome.eligible ? <>
              <p>Thank you for completing your application.</p>
              <p>Based on the information you have provided, you have successfully passed our initial eligibility screening and can proceed to the next stage of your apprenticeship application.</p>
              <p>You will receive an email shortly with instructions to activate your account and begin the enrolment process.</p>
              <p>If you have any questions, please contact our Admissions Team at <a href={`mailto:${EMPLOYMENT_CONTACT_EMAIL}`}>{EMPLOYMENT_CONTACT_EMAIL}</a>.</p>
              {outcome.outcome === 'existingAccount' ? <p>
                An account already exists for <strong>{email}</strong>. Please <Link to="/login">sign in</Link>.
                If you have not set a password yet, or have forgotten it, use <Link to="/forgot-password">Forgot password</Link>.
              </p> : outcome.outcome === 'invited' ? <p>
                We have sent an email to <strong>{email}</strong> with a link to create your password. The link expires in 7 days. If you cannot find the email, check your junk or spam folder.
              </p> : outcome.outcome === 'invitePending' ? <p>
                We could not send your invitation email just now. Our team will send you a link to create your password at <strong>{email}</strong>.
              </p> : null}
            </> : <>
              <p>Thank you for your interest in Kent Business College.</p>
              <p>Based on the information you have provided, your application does not currently meet our initial eligibility requirements.</p>
              {outcome.reasons.map(reason => <p key={reason}>Reason: {reason}</p>)}
              <p>Government-funded apprenticeships in England generally require qualifying paid employment and at least 50% of working hours to be spent in England.</p>
              <p>If your employer is based outside England but you work at an English branch, or you believe your circumstances may qualify for an exception, please contact our Admissions Team at <a href={`mailto:${EMPLOYMENT_CONTACT_EMAIL}`}>{EMPLOYMENT_CONTACT_EMAIL}</a>.</p>
            </>}
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
              if (item.kind === 'heading') {
                return <h3 key={item.key} className="border-b border-foreground-200 pb-1.5 pt-4 text-[16px] font-semibold text-foreground-900">{item.text}</h3>;
              }
              if (item.kind === 'description') {
                return <div key={item.key} className="whitespace-pre-line py-2.5 text-[13px] leading-relaxed text-foreground-600">{item.text}</div>;
              }
              if (!isQuestion(item)) {
                if (!isNoteShown(item, answers)) return null;
                const [title, ...paragraphs] = item.text.split('\n\n');
                if (item.key === EMPLOYMENT_REQUIREMENTS_NOTICE_KEY) {
                  return (
                    <div
                      key={item.key}
                      id={EMPLOYMENT_REQUIREMENTS_NOTICE_KEY}
                      className={`${styles.note} ${styles.employmentNotice}`}
                      role="note"
                      aria-live="polite"
                    >
                      <div className={styles.noticeTitle}>
                        <Info aria-hidden="true" />
                        <strong>{title}</strong>
                      </div>
                      {paragraphs.map(paragraph => (
                        <p key={paragraph}>
                          {paragraph.split(EMPLOYMENT_CONTACT_EMAIL).map((part, partIndex, parts) => (
                            <span key={`${part}-${partIndex}`}>
                              {part}
                              {partIndex < parts.length - 1 && (
                                <a href={`mailto:${EMPLOYMENT_CONTACT_EMAIL}`}>{EMPLOYMENT_CONTACT_EMAIL}</a>
                              )}
                            </span>
                          ))}
                        </p>
                      ))}
                    </div>
                  );
                }
                return (
                  <div key={item.key} className={styles.note}>
                    {paragraphs.length ? <strong>{title}</strong> : null}
                    {paragraphs.length ? paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>) : title}
                  </div>
                );
              }
              if (!isShown(item, answers)) return null;
              number += 1;
              const value = answerFor(answers, item);
              const postcode = isPostcodeQuestion(item) && (item.key !== 'postcode' || residentialUk);
              return (
                <QuestionRow
                  key={item.key}
                  question={{ ...item, required: isRequired(item, answers), hint: item.key === 'dateOfBirth'
                    ? dateNotice(item.key, answers, today) ?? item.hint
                    : [item.hint, dateNotice(item.key, answers, today)].filter(Boolean).join(' ') || undefined }}
                  number={number}
                  value={value}
                  error={errors[item.key]}
                  today={today}
                  minimumDate={minimumDate(item, answers)}
                  onChange={next => setAnswer(item.key, next)}
                  onBlur={postcode ? () => void checkPostcode(String(value ?? '')) : undefined}
                  check={postcode && isUkPostcode(String(value ?? '')) ? checks[compactPostcode(String(value))] : undefined}
                  onRetry={['employerPostcode', 'mainWorkplacePostcode'].includes(item.key) && [EMPLOYER_POSTCODE_UNAVAILABLE, WORKPLACE_POSTCODE_UNAVAILABLE].includes(errors[item.key]) ? () => void handleContinue() : undefined}
                />
              );
            })}
          </div>

          {historicalMessage && <div className={styles.note} role="status">
            <p>{historicalMessage}</p>
            <p>Please contact our Admissions Team at <a href={`mailto:${EMPLOYMENT_CONTACT_EMAIL}`}>{EMPLOYMENT_CONTACT_EMAIL}</a>.</p>
          </div>}
          {submitError && <p className={styles.alert} role="alert">{submitError}</p>}

          <div className={styles.actions}>
            {index > 0 && (
              <button type="button" className={styles.secondary} onClick={() => goTo(index - 1)} disabled={submitting}>
                <ArrowLeft aria-hidden="true" /> Previous
              </button>
            )}
            <button
              type="submit"
              className={styles.primary}
              disabled={submitting || verifyingEmployer || employmentRestricted}
              aria-busy={submitting || verifyingEmployer}
              aria-describedby={employmentRestricted ? EMPLOYMENT_REQUIREMENTS_NOTICE_KEY : undefined}
            >
              {verifyingEmployer ? 'Verifying postcode…' : last ? (submitting ? 'Submitting…' : 'Submit application') : 'Continue'}
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
        </div>
        {hero && (
          <header className={frame.hero}>
            <div className={frame.heroCopy}>
              <p className={frame.eyebrow}>{hero.eyebrow}</p>
              <h1>{hero.title}</h1>
              <p>{hero.intro}</p>
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
  minimumDate: string;
  onChange: (value: string | boolean) => void;
  onBlur?: () => void;
  /** Postcode questions: the lookup of the current answer. */
  check?: Check;
  onRetry?: () => void;
}

function QuestionRow({ question, number, value, error, today, minimumDate, onChange, onBlur, check, onRetry }: QuestionRowProps) {
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
      : question.options.map(option => ({ label: question.optionLabels?.[option] ?? option, value: option }));
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
            {question.options.map(option => <option key={option} value={option}>{question.optionLabels?.[option] ?? option}</option>)}
          </select>
        ) : (
          <input
            {...shared}
            className={`${styles.input} ${invalid ? styles.invalid : ''}`}
            type={question.kind}
            value={String(value ?? '')}
            onChange={event => onChange(event.target.value)}
            onBlur={onBlur}
            placeholder={question.placeholder}
            maxLength={'maxLength' in question ? question.maxLength : undefined}
            max={question.kind === 'date' && !question.allowFuture ? today : undefined}
            min={question.kind === 'date' ? minimumDate : undefined}
            step={question.kind === 'number' ? 'any' : undefined}
            inputMode={question.kind === 'number' ? 'decimal' : undefined}
            autoComplete={'autoComplete' in question ? question.autoComplete : undefined}
          />
        )}
        {message}
        {onRetry && <button type="button" className={styles.secondary} onClick={onRetry}>Retry postcode verification</button>}
        {locationId && (
          <div id={locationId} aria-live="polite">
            <PostcodeLocation check={check} />
            {question.key === 'employerPostcode' && check?.status === 'found' && check.country && (
              <p className={styles.location}>Country (verified from postcode): {check.country}</p>
            )}
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
