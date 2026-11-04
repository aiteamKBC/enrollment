import { useEffect, useState, type ReactNode } from 'react';
import { ageFromDob, useWizard } from "../WizardContext.helpers";
import { fetchIlrLearnerRecord, type IlrLearnerRecord } from '@/api/ilrLearnerRecord';
import { FieldRow, YesNoRadio, inputClass } from '../../components/ui';
import { LabeledInput, LabeledSelect, SignatureField, StepHeading } from './fields';
import { ILR_SIGNATURE_LABEL, ilrDetailsMissing } from '../validation';
import { FieldError, invalidClass } from '../stepErrors';
import { requiredMessage, useMissing, useMissingCheck } from "../stepErrors.helpers";
import { NI_RE } from '../layout/registry';
import { COUNTRY_OPTIONS } from '@/lib/countries';
import { StepItems, type ItemRenderer } from '../layout/StepItems';
import { useStepTitle } from '../layout/useLayout';
import { useText } from '../layout/textsContext';
import { RichText } from '../layout/RichText';
import type { IlrLearnerDetails as IlrLearnerDetailsState } from '../../types';
import EnrolmentSignature from './EnrolmentSignature';
import { LEARNER_TITLE_OPTIONS } from '@/lib/learnerTitles';
import { HEALTH_CONDITIONS, NONDISCLOSURE_CODES, OTHER_HEALTH_CODES, healthLabel } from '../ilrHealthOptions';
import {
  BENEFIT_CLAIM_BASIS_OPTIONS,
  EMPLOYMENT_STATUS_OPTIONS,
  ETHNICITY_OPTIONS,
  IN_PAID_EMPLOYMENT,
  LEGAL_SEX_OPTIONS,
  LENGTH_OF_UNEMPLOYMENT_OPTIONS,
  NOT_IN_PAID_EMPLOYMENT,
  NO_BENEFITS,
  PRIOR_ATTAINMENT_OPTIONS,
  STATE_BENEFIT_OPTIONS,
  labels,
} from '../ilrLearnerDetailsOptions';

/**
 * ILR Learner Details — the Individualised Learner Record's learner details,
 * health, education and employment questions, and its privacy declaration.
 *
 * Separate from the Extended ILR step and stored separately
 * (enrolment."Wizard_Ilr_Learner_Details"). Anything the learner record already
 * holds — name, DOB, current address, phone, email — is shown read-only from
 * it rather than retyped, so the ILR cannot disagree with the record.
 */

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-6">
      <h3 className="mb-2 border-b-2 border-primary-100 pb-1.5 font-heading text-[14px] font-semibold text-foreground-900">
        <span className="border-b-2 border-primary-500 pb-1.5">{title}</span>
      </h3>
      {children}
    </section>
  );
}

/** A Yes/No with the wizard's required star (when required), red while unanswered after Next. */
function RequiredYesNo({ missingKey, required = true, ...props }: { legend: string; name: string; value: boolean | null; onChange: (v: boolean) => void; missingKey: string; required?: boolean }) {
  const missing = useMissing(missingKey);
  return <YesNoRadio {...props} legend={required ? `${props.legend} *` : props.legend} error={missing ? requiredMessage(props.legend, 'choose') : undefined} />;
}

/** "DD/MM/YYYY" for a stored ISO date, or the value as stored. */
function displayDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : value;
}

export default function IlrLearnerDetails() {
  const { userId, isCommercial, draft, setSection, board, layout, ilrFinalisedAt, ilrLegacyReadOnly, readOnly } = useWizard();
  const locked = Boolean((ilrFinalisedAt || ilrLegacyReadOnly) && !isCommercial && !readOnly);
  const title = useStepTitle('ilr-details', 'ILR');
  const t = useText();
  const missing = useMissingCheck();
  const d = draft.ilrDetails;
  const set = (patch: Partial<IlrLearnerDetailsState>) => setSection('ilrDetails', { ...d, ...patch });

  const [record, setRecord] = useState<IlrLearnerRecord | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [healthSearch, setHealthSearch] = useState('');
  useEffect(() => {
    let cancelled = false;
    fetchIlrLearnerRecord(isCommercial ? 'commercial' : 'apprenticeship', userId)
      .then((r) => { if (!cancelled) setRecord(r); })
      .catch((e: Error) => { if (!cancelled) setRecordError(e.message); });
    return () => { cancelled = true; };
  }, [userId, isCommercial]);

  // "Since birth" means the years at this address are the learner's age, from
  // the date of birth on their record (or their Personal details as a fallback).
  const ageYears = ageFromDob(record?.dateOfBirth || draft.personalDetails.dob) ?? null;

  // Everything this step fills in by itself, as ONE update: separate updates in
  // the same render would each start from the same stale section and the later
  // one would undo the earlier. An answer the learner already gave is kept.
  //  * the prior postcode starts as the current one (most learners have not
  //    moved since enrolling), and the NI number as the one staff verified;
  //  * "Since birth" keeps the years in step with the learner's age.
  // The certification signature is deliberately NOT filled in: the learner
  // signs it themselves, by clicking to sign.
  useEffect(() => {
    const fill: Partial<IlrLearnerDetailsState> = {};
    if (record?.currentPostcode && !d.postcodePriorToEnrolment) fill.postcodePriorToEnrolment = record.currentPostcode;
    if (record?.nationalInsuranceNumber && !d.niNumber) fill.niNumber = record.nationalInsuranceNumber;
    if (d.sinceBirth === true && ageYears !== null && d.yearsAtAddress !== ageYears) fill.yearsAtAddress = ageYears;
    if (!locked && Object.keys(fill).length) set(fill);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record, ageYears, d.sinceBirth]);

  const niError = d.niNumber.trim() && !NI_RE.test(d.niNumber.trim())
    ? 'Enter a valid National Insurance number, for example AB 12 34 56 C'
    : '';
  const legalSexMissing = Boolean(missing('Legal Sex'));
  const yearsMissing = Boolean(missing('How long have you been at this address (years)?'));
  const benefitsMissing = Boolean(missing('State benefits'));
  const healthMissing = Boolean(missing('Health conditions'));
  const primaryHealthMissing = Boolean(missing('Primary health condition'));
  const hoursMissing = Boolean(missing('Working hours per week'));
  const niAppliedMissing = Boolean(missing('NI number application confirmation'));
  const signatoryName = record ? [record.givenNames, record.familyName].filter(Boolean).join(' ') : '';
  // The certification comes last: clicking to sign while a required question
  // is unanswered lists what is missing instead. Same rule as the step's
  // completeness check, so the two cannot disagree.
  const missingBeforeSign = ilrDetailsMissing(draft, layout).filter((label) => ![ILR_SIGNATURE_LABEL, 'Your signature'].includes(label));
  const noop = () => {};

  // Follow-up questions depend on earlier answers. Changing an answer clears the
  // follow-ups it no longer asks, so a hidden question never keeps a stale value.
  const inPaidEmployment = d.employmentStatus === IN_PAID_EMPLOYMENT;
  const notInPaidEmployment = NOT_IN_PAID_EMPLOYMENT.includes(d.employmentStatus);
  const claimsBenefit = Boolean(d.stateBenefits) && d.stateBenefits !== NO_BENEFITS;
  const selectedHealthCodes = d.healthConditionCodes ?? [];
  const nondisclosure = selectedHealthCodes.some(code => NONDISCLOSURE_CODES.has(code));
  const needsSupportDetail = selectedHealthCodes.some(code => OTHER_HEALTH_CODES.has(code));
  const toggleHealthCode = (code: string) => {
    const selected = selectedHealthCodes.includes(code);
    const next = selected ? selectedHealthCodes.filter(value => value !== code)
      : NONDISCLOSURE_CODES.has(code) ? [code]
      : [...selectedHealthCodes.filter(value => !NONDISCLOSURE_CODES.has(value)), code];
    set({
      healthConditionCodes: next,
      primaryHealthConditionCode: next.includes(d.primaryHealthConditionCode ?? '') ? d.primaryHealthConditionCode : '',
      additionalSupportDetails: next.some(value => OTHER_HEALTH_CODES.has(value)) ? d.additionalSupportDetails ?? '' : '',
    });
  };
  const changeEmploymentStatus = (status: string) => set({
    employmentStatus: status,
    ...(status === IN_PAID_EMPLOYMENT ? {} : { employmentStartDate: '', jobTitle: '', selfEmployed: null, workingHoursPerWeek: '' }),
    ...(NOT_IN_PAID_EMPLOYMENT.includes(status)
      // Preselected, as on the paper form: most learners out of work have been
      // for under six months.
      ? { lengthOfUnemployment: d.lengthOfUnemployment || LENGTH_OF_UNEMPLOYMENT_OPTIONS[0].label }
      : { lengthOfUnemployment: '', volunteers: null }),
  });

  // One renderer per item in layout/registry.ts; the layout decides order,
  // which are shown and which are required.
  const renderers: Record<string, ItemRenderer> = {
    // Kept as a layout/validation key for published layouts. The visible
    // control is rendered inside ild.record so it sits after learner names.
    'ild.title': () => null,
    'pd.signature': () => <EnrolmentSignature />,
    'ild.h.learner': ({ children }) => (
      <Section title={t('ild.h.learner.title')}>
        {recordError && (
          <p className="mb-2 text-[12px] text-red-600"><i className="ri-error-warning-line mr-1" />{recordError}</p>
        )}
        <p className="mb-2 text-[12px] text-foreground-500">{t('ild.h.learner.intro')}</p>
        {children}
      </Section>
    ),
    'ild.record': () => (
      <>
        <LabeledInput label={t('ild.record.familyName')} value={record?.familyName ?? ''} onChange={noop} readOnly />
        <LabeledInput label={t('ild.record.givenNames')} value={record?.givenNames ?? ''} onChange={noop} readOnly />
        {!isCommercial && <LabeledSelect
          label={t('ild.title.label')}
          ariaLabel={t('ild.title.label')}
          value={draft.personalDetails.title ?? ''}
          options={draft.personalDetails.title && !LEARNER_TITLE_OPTIONS.includes(draft.personalDetails.title)
            ? [draft.personalDetails.title, ...LEARNER_TITLE_OPTIONS] : LEARNER_TITLE_OPTIONS}
          required={Boolean(draft.personalDetails.title !== undefined)}
          missingKey="Title"
          onChange={value => setSection('personalDetails', { ...draft.personalDetails, title: value })}
        />}
        <LabeledInput label={t('ild.record.dateOfBirth')} value={displayDate(record?.dateOfBirth ?? '')} onChange={noop} readOnly />
        {!isCommercial && <>
          <LabeledSelect label="Country of residence" value={draft.personalDetails.country ?? record?.country ?? ''}
            options={draft.personalDetails.country && !COUNTRY_OPTIONS.includes(draft.personalDetails.country)
              ? [draft.personalDetails.country, ...COUNTRY_OPTIONS] : COUNTRY_OPTIONS}
            onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, country: value })} />
          <LabeledInput label={t('ild.record.address1')} value={draft.personalDetails.addressLine1 ?? record?.addressLine1 ?? ''}
            onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, addressLine1: value })} />
          <LabeledInput label={t('ild.record.address2')} value={draft.personalDetails.addressLine2 ?? record?.addressLine2 ?? ''}
            onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, addressLine2: value })} />
          <LabeledInput label={t('ild.record.address3')} value={draft.personalDetails.addressLine3 ?? record?.addressLine3 ?? ''}
            onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, addressLine3: value })} />
          <LabeledInput label={t('ild.record.address4')} value={draft.personalDetails.addressLine4 ?? record?.addressLine4 ?? ''}
            onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, addressLine4: value })} />
        </>}
      </>
    ),
    'ild.currentPostcode': () => (
      <LabeledInput label={t('ild.record.currentPostcode')} missingKey="Current postcode" required
        value={draft.personalDetails.currentPostcode ?? record?.currentPostcode ?? ''}
        onChange={(value) => setSection('personalDetails', { ...draft.personalDetails, currentPostcode: value.toUpperCase() })}
      />
    ),
    'ild.yearsAtAddress': ({ required }) => (
      <FieldRow label={t('ild.yearsAtAddress.label')} required={required}>
        <div className="flex flex-wrap items-center gap-4">
          <input
            type="number"
            min={0}
            max={120}
            aria-label="Years at this address"
            aria-invalid={yearsMissing || undefined}
            value={d.sinceBirth ? ageYears ?? '' : d.yearsAtAddress ?? ''}
            disabled={d.sinceBirth === true}
            title={d.sinceBirth ? 'Worked out from your date of birth' : undefined}
            onChange={(e) => {
              const n = e.target.value === '' ? null : Math.max(0, Math.min(120, Math.floor(Number(e.target.value))));
              set({ yearsAtAddress: Number.isNaN(n) ? null : n });
            }}
            // Grey when worked out from the date of birth, like the other
            // values this step fills in itself.
            className={`${inputClass} !w-28 disabled:!bg-background-200 disabled:!border-foreground-200 disabled:!text-foreground-600 disabled:cursor-default${yearsMissing ? invalidClass : ''}`}
          />
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-[12px] text-foreground-700">
            <input
              type="checkbox"
              checked={d.sinceBirth === true}
              // Ticking fills the years with the learner's age; unticking
              // clears it for them to type how long they have lived there.
              onChange={(e) => set({ sinceBirth: e.target.checked, yearsAtAddress: e.target.checked ? ageYears : null })}
              className="accent-primary-500"
            />
            {t('ild.yearsAtAddress.sinceBirth')}
          </label>
        </div>
      </FieldRow>
    ),
    'ild.telephone': () => <LabeledInput label={t('ild.telephone.label')}
      value={isCommercial ? record?.telephone ?? '' : draft.personalDetails.phone ?? record?.telephone ?? ''}
      onChange={isCommercial ? noop : (value) => setSection('personalDetails', { ...draft.personalDetails, phone: value })}
      readOnly={isCommercial} />,
    'ild.postcodePrior': ({ required }) => (
      <LabeledInput
        label={t('ild.postcodePrior.label')}
        missingKey="Postcode prior to enrolment"
        required={required}
        value={d.postcodePriorToEnrolment}
        onChange={(v) => set({ postcodePriorToEnrolment: v.toUpperCase() })}
        placeholder="CT1 1AA"
      />
    ),
    'ild.ni': () => (
      <>
        <FieldRow label={t('ild.ni.label')}>
          <input
            value={d.niNumber}
            onChange={(e) => set({ niNumber: e.target.value.toUpperCase(), niApplied: e.target.value.trim() ? null : d.niApplied })}
            placeholder="AB 12 34 56 C"
            aria-invalid={niError ? true : undefined}
            className={`${inputClass}${niError ? invalidClass : ''}`}
          />
          {niError && <FieldError message={niError} />}
        </FieldRow>
        {!isCommercial && !d.niNumber.trim() && <div className="mb-4 rounded-lg border border-foreground-100 bg-background-50 px-4 py-3 text-[12px] leading-relaxed text-foreground-700">
          <p>{t('ild.ni.help')}</p>
          <label className="mt-2 flex cursor-pointer items-start justify-between gap-3">
            <span>{t('ild.ni.confirm')}</span>
            <input type="checkbox" checked={d.niApplied === true} onChange={event => set({ niApplied: event.target.checked })}
              aria-invalid={niAppliedMissing || undefined} className="mt-0.5 shrink-0 accent-primary-500" />
          </label>
          {niAppliedMissing && <FieldError message="Confirm that you have applied for a National Insurance number." />}
        </div>}
      </>
    ),
    'ild.niApplied': () => null,
    'ild.email': () => <LabeledInput label={t('ild.email.label')} value={record?.email ?? ''} onChange={noop} readOnly />,
    'ild.legalSex': ({ required }) => (
      <FieldRow label={t('ild.legalSex.label')} required={required}>
        <div role="radiogroup" aria-label={t('ild.legalSex.label')} className={`inline-flex items-center gap-5${legalSexMissing ? ' rounded-lg border border-red-500 px-2.5 py-1' : ''}`}>
          {LEGAL_SEX_OPTIONS.map((option) => (
            <label key={option} className="flex cursor-pointer items-center gap-1.5 text-[12px] text-foreground-700">
              <input
                type="radio"
                name="ilr-legal-sex"
                checked={d.legalSex === option}
                onChange={() => set({ legalSex: option })}
                aria-invalid={legalSexMissing || undefined}
                className="accent-primary-500"
              />
              {option}
            </label>
          ))}
        </div>
        {legalSexMissing && <div><FieldError message="Please select your legal sex." /></div>}
      </FieldRow>
    ),
    'ild.pronouns': ({ required }) => (
      <LabeledInput label={t('ild.pronouns.label')} missingKey="What pronouns do you use?" required={required} value={d.pronouns} onChange={(v) => set({ pronouns: v })} />
    ),
    'ild.ethnicity': ({ required }) => (
      <LabeledSelect label={t('ild.ethnicity.label')} missingKey="Ethnicity" required={required} value={d.ethnicity} options={labels(ETHNICITY_OPTIONS)} onChange={(v) => set({ ethnicity: v })} />
    ),
    'ild.h.health': ({ children }) => <Section title={t('ild.h.health.title')}>{children}</Section>,
    'ild.disability': ({ required }) => (
      <RequiredYesNo
        legend={t('ild.disability.label')}
        name="ilr-disability"
        missingKey="Long-term disability, health problem or learning difficulty"
        required={required}
        value={d.longTermDisability}
        onChange={(v) => set({ longTermDisability: v, ...(v ? {} : {
          healthConditionCodes: [], primaryHealthConditionCode: '', additionalSupportDetails: '',
        }) })}
      />
    ),
    'ild.healthConditions': () => d.longTermDisability === true && !isCommercial && (
      <FieldRow label={t('ild.healthConditions.label')} required>
        <div className={`rounded-lg border bg-background-50 p-3 ${healthMissing ? 'border-red-500' : 'border-foreground-200'}`}>
          {selectedHealthCodes.length > 0 && <div className="mb-3 flex flex-wrap gap-2" aria-label="Selected health conditions">
            {selectedHealthCodes.map(code => <span key={code} className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-primary-100 px-2.5 py-1 text-[12px] text-foreground-900">
              <span className="truncate">{healthLabel(code)}</span>
              <button type="button" onClick={() => toggleHealthCode(code)} aria-label={`Remove ${healthLabel(code)}`}
                className="rounded-full px-1 font-semibold hover:bg-primary-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500">×</button>
            </span>)}
          </div>}
          {selectedHealthCodes.includes('15') && <p className="mb-2 text-[12px] text-foreground-700">
            Asperger’s syndrome has a historical ILR code that expired on 31 July 2025. The enrolment team will review how it is reported.
          </p>}
          <label className="block text-[12px] font-medium text-foreground-700" htmlFor="ilr-health-search">Search health conditions</label>
          <input id="ilr-health-search" type="search" value={healthSearch} onChange={event => setHealthSearch(event.target.value)}
            placeholder="Type to filter the list" className={`${inputClass} mt-1 w-full`} />
          <div role="group" className="mt-2 max-h-48 space-y-0.5 overflow-y-auto" aria-label="Health condition options">
            {HEALTH_CONDITIONS.filter(option => option.label.toLowerCase().includes(healthSearch.trim().toLowerCase())).map(option => (
              <label key={option.code} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-[12px] text-foreground-800 hover:bg-primary-50">
                <input type="checkbox" checked={selectedHealthCodes.includes(option.code)} onChange={() => toggleHealthCode(option.code)}
                  className="mt-0.5 accent-primary-500" />
                <span>{option.label}{'expired' in option && <span className="ml-1 text-foreground-500">(historical ILR code)</span>}</span>
              </label>
            ))}
            {!HEALTH_CONDITIONS.some(option => option.label.toLowerCase().includes(healthSearch.trim().toLowerCase())) &&
              <p className="px-2 py-2 text-[12px] text-foreground-600">No matching conditions.</p>}
          </div>
        </div>
        {healthMissing && <FieldError message="Select at least one health condition or a nondisclosure option." />}
      </FieldRow>
    ),
    'ild.primaryHealth': () => d.longTermDisability === true && !isCommercial && !nondisclosure && selectedHealthCodes.length > 0 && (
      <FieldRow label={t('ild.primaryHealth.label')} required>
        <select value={d.primaryHealthConditionCode ?? ''} onChange={event => set({ primaryHealthConditionCode: event.target.value })}
          aria-label={t('ild.primaryHealth.label')}
          aria-invalid={primaryHealthMissing || undefined} className={`${inputClass} cursor-pointer${primaryHealthMissing ? invalidClass : ''}`}>
          <option value="">Select primary condition</option>
          {selectedHealthCodes.map(code => <option key={code} value={code}>{healthLabel(code)}</option>)}
        </select>
        {primaryHealthMissing && <FieldError message="Choose one of your selected health conditions." />}
      </FieldRow>
    ),
    'ild.additionalSupport': () => d.longTermDisability === true && !isCommercial && needsSupportDetail && (
      <FieldRow label={t('ild.additionalSupport.label')}>
        <textarea value={d.additionalSupportDetails ?? ''} onChange={event => set({ additionalSupportDetails: event.target.value })}
          aria-label={t('ild.additionalSupport.label')}
          maxLength={4000} rows={4} className={`${inputClass} min-h-24 w-full`} />
        <p className="mt-1 text-[12px] text-foreground-600">Only share what would help us support your learning.</p>
      </FieldRow>
    ),
    'ild.h.education': ({ children }) => <Section title={t('ild.h.education.title')}>{children}</Section>,
    'ild.highestQualification': ({ required }) => (
      <LabeledSelect
        label={t('ild.highestQualification.label')}
        missingKey="Highest qualification achieved"
        required={required}
        value={d.highestQualification}
        options={labels(PRIOR_ATTAINMENT_OPTIONS)}
        onChange={(v) => set({ highestQualification: v })}
      />
    ),
    'ild.h.employment': ({ children }) => <Section title={t('ild.h.employment.title')}>{children}</Section>,
    'ild.employmentStatus': ({ required }) => (
      <>
        <LabeledSelect label={t('ild.employmentStatus.label')} missingKey="Employment status" required={required} value={d.employmentStatus} options={labels(EMPLOYMENT_STATUS_OPTIONS)} onChange={changeEmploymentStatus} />
        {inPaidEmployment && (
          <>
            <LabeledInput
              label={t('ild.employmentStatus.startDate')}
              missingKey="Start date with employer"
              type="date"
              required={required}
              value={d.employmentStartDate ?? ''}
              onChange={(v) => set({ employmentStartDate: v })}
            />
            <LabeledInput label={t('ild.employmentStatus.jobTitle')} value={d.jobTitle ?? ''} onChange={(v) => set({ jobTitle: v })} />
            <YesNoRadio legend={t('ild.employmentStatus.selfEmployed')} name="ilr-self-employed" value={d.selfEmployed ?? null} onChange={(v) => set({ selfEmployed: v })} />
          </>
        )}
      </>
    ),
    'ild.fullTimeEducation': ({ required }) => (
      <>
        <RequiredYesNo
          legend={t('ild.fullTimeEducation.label')}
          name="ilr-fulltime"
          missingKey="Full-time education or training"
          required={required}
          value={d.fullTimeEducation}
          onChange={(v) => set({ fullTimeEducation: v, expectedLeavingDate: v ? d.expectedLeavingDate : '' })}
        />
        {d.fullTimeEducation === true && (
          <LabeledInput
            label={t('ild.fullTimeEducation.leavingDate')}
            type="date"
            value={d.expectedLeavingDate ?? ''}
            onChange={(v) => set({ expectedLeavingDate: v })}
          />
        )}
        {/* Asked of learners not in paid employment. Kept with this item, as
            before, so the questions read in their original order. */}
        {notInPaidEmployment && (
          <>
            <LabeledSelect
              label={t('ild.fullTimeEducation.unemployment')}
              value={d.lengthOfUnemployment ?? ''}
              options={labels(LENGTH_OF_UNEMPLOYMENT_OPTIONS)}
              onChange={(v) => set({ lengthOfUnemployment: v })}
            />
            <YesNoRadio legend={t('ild.fullTimeEducation.volunteer')} name="ilr-volunteer" value={d.volunteers ?? null} onChange={(v) => set({ volunteers: v })} />
          </>
        )}
      </>
    ),
    'ild.workingHours': () => !isCommercial && inPaidEmployment && (
      <FieldRow label={t('ild.workingHours.label')} required>
        <input type="number" min="0.01" max="168" step="0.01" inputMode="decimal"
          value={d.workingHoursPerWeek ?? ''} onChange={event => set({ workingHoursPerWeek: event.target.value })}
          aria-label={t('ild.workingHours.label')} aria-invalid={hoursMissing || undefined}
          className={`${inputClass} !w-32${hoursMissing ? invalidClass : ''}`} />
        {hoursMissing && <FieldError message="Enter weekly working hours greater than 0 and no more than 168 (up to two decimal places)." />}
      </FieldRow>
    ),
    'ild.stateBenefits': ({ required }) => (
      <>
        <FieldRow label={t('ild.stateBenefits.label')} required={required}>
          <select
            value={d.stateBenefits}
            onChange={(e) => set({ stateBenefits: e.target.value, benefitClaimBasis: e.target.value === NO_BENEFITS ? '' : d.benefitClaimBasis })}
            aria-invalid={benefitsMissing || undefined}
            className={`${inputClass} cursor-pointer${benefitsMissing ? invalidClass : ''}`}
          >
            {STATE_BENEFIT_OPTIONS.map((o) => <option key={o.label} value={o.label}>{o.label}</option>)}
          </select>
        </FieldRow>
        {claimsBenefit && (
          <LabeledSelect
            label={t('ild.stateBenefits.claimBasis')}
            value={d.benefitClaimBasis ?? ''}
            options={BENEFIT_CLAIM_BASIS_OPTIONS}
            onChange={(v) => set({ benefitClaimBasis: v })}
            placeholder=""
          />
        )}
      </>
    ),
    'ild.h.declaration': ({ children }) => <Section title={t('ild.h.declaration.title')}>{children}</Section>,
    'ild.declaration': () => (
      <>
        <RichText
          source={t('ild.declaration.privacy')}
          classes={{
            container: 'max-h-80 space-y-3 overflow-y-auto rounded-lg border border-foreground-100 bg-background-50 p-4 text-[12px] leading-relaxed text-foreground-700',
            list: 'mt-1 list-disc space-y-0.5 pl-5',
            link: 'text-primary-600 underline break-all',
          }}
        />
        <RichText
          source={t('ild.declaration.college')}
          classes={{
            container: 'mt-4 space-y-1 rounded-lg border border-foreground-100 p-4 text-[12px] leading-relaxed text-foreground-700',
            link: 'text-primary-600 underline',
          }}
        />
      </>
    ),
    'ild.signature': ({ required }) => (
      <>
        <p className="mt-4 text-[13px] font-medium text-foreground-800">
          {t('ild.signature.certify')} {required && <span className="text-red-500">*</span>}
        </p>
        {isCommercial && <SignatureField
          label={t('ild.signature.label')}
          signatoryName={signatoryName}
          // The learner's saved signature — from Personal details, or the one
          // they gave at first sign-in. Applied only when they click to sign.
          savedValue={draft.personalDetails.signature || board.contact.savedSignature}
          missingBeforeSign={missingBeforeSign}
          missingKey={ILR_SIGNATURE_LABEL}
          value={d.signature ?? undefined}
          onChange={(v) => set({
            signature: v || null,
            signatureDate: v && !d.signatureDate ? new Date().toISOString().slice(0, 10) : v ? d.signatureDate : '',
          })}
        />}
      </>
    ),
  };

  return (
    <div>
      <StepHeading title={title} subtitle={t('step.ilr-details.subtitle')} />
      <p className="mb-4 text-[13px] text-foreground-700" role="status">{locked
        ? ilrFinalisedAt ? 'Your ILR has been signed and submitted. You can view your information, but you can no longer make changes.'
          : 'This historical ILR is read-only pending review. Its original signing state has not been changed.'
        : 'Please review your information carefully. Once you sign and continue, you will not be able to edit your ILR.'}</p>
      <fieldset disabled={locked} className="min-w-0 border-0 p-0"><StepItems slug="ilr-details" renderers={renderers} /></fieldset>
    </div>
  );
}
