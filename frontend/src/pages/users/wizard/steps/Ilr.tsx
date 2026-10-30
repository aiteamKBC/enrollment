import type { ReactNode } from 'react';
import { useWizard } from "../WizardContext.helpers";
import { useToast } from "@/hooks/useToast.helpers";
import { COUNTRY_OPTIONS, NATIONALITY_OPTIONS } from '@/lib/countries';
import { YES_NO_SELECT } from '@/mocks/enrolment-console';
import { YesNoRadio, inputClass, btnPrimary, btnSecondary } from '../../components/ui';
import IlrEligibilityEvidence from './IlrEligibilityEvidence';
import { useEmployerDetailsPrefill } from './useEmployerDetailsPrefill';
import { LabeledInput, LabeledSelect, LabeledTextarea, StepHeading } from './fields';
import { downloadIlrDocument } from './ilrDocument';
import { IlrText } from './ilrAdditionalText';
import { FieldError, invalidClass } from '../stepErrors';
import { requiredMessage, useMissingCheck } from "../stepErrors.helpers";
import { StepItems, type ItemRenderer } from '../layout/StepItems';
import { useLayout, useStepTitle } from '../layout/useLayout';
import { useText } from '../layout/textsContext';
import { RichText } from '../layout/RichText';

function Fieldset({ legend, intro, children }: { legend: string; intro?: string; children: ReactNode }) {
  return (
    <fieldset className="border border-foreground-100 rounded-xl p-4 mb-4">
      <legend className="text-[13px] font-heading font-semibold text-foreground-800 px-1">{legend}</legend>
      {intro && <p className="text-[12px] text-foreground-500 mb-3 leading-relaxed">{intro}</p>}
      {children}
    </fieldset>
  );
}

const EVIDENCE_KEY = 'Proof of identification and residency';

export default function Ilr() {
  const { board, draft, setSection, saveIlr, ilrSaving, ilrSavedAt, fileIlrDocument, ilrFiling } = useWizard();
  const layout = useLayout();
  const { success, error } = useToast();
  const ilr = draft.ilr;
  const set = (patch: Partial<typeof ilr>) => setSection('ilr', { ...ilr, ...patch });
  const title = useStepTitle('ilr', 'Extended ILR');
  const t = useText();
  // Empty Employer Details are filled from the learner's employer and organisation.
  useEmployerDetailsPrefill();
  // Red Yes/No rows once Next is pressed with them unanswered.
  const missing = useMissingCheck();
  const yn = (key: string) => (missing(key) ? requiredMessage('', 'choose') : undefined);
  const evidenceMissing = Boolean(missing(EVIDENCE_KEY));

  const ai = ilr.additionalInformation;
  const setAi = (patch: Partial<typeof ai>) => set({ additionalInformation: { ...ai, ...patch } });

  // The learning and provider declarations (and their signature blocks) are no
  // longer on this step, so the Personal details signature is no longer copied
  // into the ILR either: it would sign a declaration the learner never saw.

  // Save the answers and file the PDF together: a filed document that doesn't
  // match the stored answers would be worse than not filing at all.
  const saveAndFile = async () => {
    try {
      await saveIlr();
      await fileIlrDocument();
      success('Document filed', 'The ILR PDF is now under Compliance documents on this learner’s profile.');
    } catch (e) {
      error('Could not file the ILR document', e instanceof Error ? e.message : 'Unexpected error');
    }
  };

  // One renderer per item in layout/registry.ts; the layout decides order,
  // which are shown and which are required.
  const renderers: Record<string, ItemRenderer> = {
    'xilr.h.contact': ({ children }) => <Fieldset legend={t('xilr.h.contact.title')} intro={t('xilr.h.contact.intro')}>{children}</Fieldset>,
    'xilr.contactPost': () => <YesNoRadio legend={t('xilr.contactPost.label')} name="c-post" error={yn('Contact by post')} value={ilr.contact.byPost} onChange={(v) => set({ contact: { ...ilr.contact, byPost: v } })} />,
    'xilr.contactPhone': () => <YesNoRadio legend={t('xilr.contactPhone.label')} name="c-phone" error={yn('Contact by phone')} value={ilr.contact.byPhone} onChange={(v) => set({ contact: { ...ilr.contact, byPhone: v } })} />,
    'xilr.contactEmail': () => <YesNoRadio legend={t('xilr.contactEmail.label')} name="c-email" error={yn('Contact by e-mail')} value={ilr.contact.byEmail} onChange={(v) => set({ contact: { ...ilr.contact, byEmail: v } })} />,

    'xilr.h.nextOfKin': ({ children }) => <Fieldset legend={t('xilr.h.nextOfKin.title')}>{children}</Fieldset>,
    'xilr.nokName': () => <LabeledInput label={t('xilr.nokName.label')} missingKey="Next of kin — full name" value={ilr.nextOfKin.fullName} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, fullName: v } })} />,
    'xilr.nokRelationship': () => <LabeledInput label={t('xilr.nokRelationship.label')} missingKey="Next of kin — relationship" value={ilr.nextOfKin.relationship} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, relationship: v } })} />,
    'xilr.nokEmail': () => <LabeledInput label={t('xilr.nokEmail.label')} type="email" missingKey="Next of kin — email" value={ilr.nextOfKin.email} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, email: v } })} />,
    'xilr.nokPhone': () => <LabeledInput label={t('xilr.nokPhone.label')} type="tel" missingKey="Next of kin — phone" value={ilr.nextOfKin.phone} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, phone: v } })} />,
    'xilr.nokSameAddress': () => (
      <>
        {/* Answering Yes clears the contact's own address, so a hidden answer is never saved or printed. */}
        <YesNoRadio legend={t('xilr.nokSameAddress.label')} name="nok-same" error={yn('Next of kin — same address')} value={ilr.nextOfKin.sameAddressAsLearner} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, sameAddressAsLearner: v, ...(v ? { postcode: '', address: '' } : {}) } })} />
        {ilr.nextOfKin.sameAddressAsLearner === false && (
          <>
            <LabeledInput label={t('xilr.nokSameAddress.postcode')} placeholder="Post Code" missingKey="Next of kin — postcode" value={ilr.nextOfKin.postcode ?? ''} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, postcode: v } })} />
            <LabeledInput label={t('xilr.nokSameAddress.address')} missingKey="Next of kin — address" value={ilr.nextOfKin.address ?? ''} onChange={(v) => set({ nextOfKin: { ...ilr.nextOfKin, address: v } })} />
          </>
        )}
      </>
    ),

    'xilr.h.eligibility': ({ children }) => <Fieldset legend={t('xilr.h.eligibility.title')}>{children}</Fieldset>,
    'xilr.employedInEngland': () => <YesNoRadio legend={t('xilr.employedInEngland.label')} name="e-eng" error={yn('Employed in England')} value={ilr.eligibility.employedInEngland} onChange={(v) => set({ eligibility: { ...ilr.eligibility, employedInEngland: v } })} />,
    'xilr.countryOfResidence': () => <LabeledSelect label={t('xilr.countryOfResidence.label')} missingKey="Country of residence" value={ilr.eligibility.countryOfResidence} options={COUNTRY_OPTIONS} onChange={(v) => set({ eligibility: { ...ilr.eligibility, countryOfResidence: v } })} />,
    'xilr.ukEeaNational': () => <YesNoRadio legend={t('xilr.ukEeaNational.label')} name="e-ukeea" error={yn('UK / EEA national')} value={ilr.eligibility.ukEeaNational} onChange={(v) => set({ eligibility: { ...ilr.eligibility, ukEeaNational: v } })} />,
    'xilr.nationality': () => <LabeledSelect label={t('xilr.nationality.label')} missingKey="Nationality" value={ilr.eligibility.nationality} options={NATIONALITY_OPTIONS} onChange={(v) => set({ eligibility: { ...ilr.eligibility, nationality: v } })} />,
    'xilr.resident3Years': () => <YesNoRadio legend={t('xilr.resident3Years.label')} name="e-res3" error={yn('Resident for the previous 3 years')} value={ilr.eligibility.residentPrev3Years} onChange={(v) => set({ eligibility: { ...ilr.eligibility, residentPrev3Years: v } })} />,
    'xilr.yearsInUk': () => <LabeledInput label={t('xilr.yearsInUk.label')} type="number" missingKey="Years in the UK" value={ilr.eligibility.yearsInUk != null ? String(ilr.eligibility.yearsInUk) : ''} onChange={(v) => set({ eligibility: { ...ilr.eligibility, yearsInUk: v ? Number(v) : undefined } })} />,
    'xilr.workPermit': () => <YesNoRadio legend={t('xilr.workPermit.label')} name="e-wp" error={yn('Requires a work permit')} value={ilr.eligibility.requiresWorkPermit} onChange={(v) => set({ eligibility: { ...ilr.eligibility, requiresWorkPermit: v } })} />,
    'xilr.evidence': () => (
      <div className="pt-3">
        <RichText
          source={t('xilr.evidence.instructions')}
          classes={{ container: 'mb-2 space-y-2 text-[12px] leading-relaxed text-foreground-500' }}
        />
        <textarea
          rows={2}
          value={ilr.eligibility.evidenceDescription}
          onChange={(e) => set({ eligibility: { ...ilr.eligibility, evidenceDescription: e.target.value } })}
          placeholder={t('xilr.evidence.placeholder')}
          aria-invalid={evidenceMissing || undefined}
          className={`${inputClass} mb-2${evidenceMissing ? invalidClass : ''}`}
        />
        {evidenceMissing && <div className="mb-2"><FieldError message="Please describe the evidence provided." /></div>}
        {/* Stored in Azure with its blob path on the Extended_ILR row, not in the answers. */}
        <IlrEligibilityEvidence />
      </div>
    ),

    'xilr.h.employer': ({ children }) => <Fieldset legend={t('xilr.h.employer.title')} intro={t('xilr.h.employer.intro')}>{children}</Fieldset>,
    'xilr.employerName': () => <LabeledInput label={t('xilr.employerName.label')} missingKey="Employer — organisation name" value={ilr.employer.organisationName} onChange={(v) => set({ employer: { ...ilr.employer, organisationName: v } })} />,
    'xilr.employerPostcode': () => <LabeledInput label={t('xilr.employerPostcode.label')} missingKey="Employer — postcode" value={ilr.employer.postcode} onChange={(v) => set({ employer: { ...ilr.employer, postcode: v } })} />,
    'xilr.employerAddress': () => <LabeledInput label={t('xilr.employerAddress.label')} missingKey="Employer — address" value={ilr.employer.address} onChange={(v) => set({ employer: { ...ilr.employer, address: v } })} />,
    'xilr.employerCity': () => <LabeledInput label={t('xilr.employerCity.label')} missingKey="Employer — city" value={ilr.employer.city} onChange={(v) => set({ employer: { ...ilr.employer, city: v } })} />,
    'xilr.lineManagerName': () => <LabeledInput label={t('xilr.lineManagerName.label')} missingKey="Line manager — name" value={ilr.employer.lineManagerName} onChange={(v) => set({ employer: { ...ilr.employer, lineManagerName: v } })} />,
    'xilr.lineManagerEmail': () => <LabeledInput label={t('xilr.lineManagerEmail.label')} type="email" missingKey="Line manager — email" value={ilr.employer.lineManagerEmail} onChange={(v) => set({ employer: { ...ilr.employer, lineManagerEmail: v } })} />,
    'xilr.lineManagerPhone': () => <LabeledInput label={t('xilr.lineManagerPhone.label')} type="tel" missingKey="Line manager — phone" value={ilr.employer.lineManagerPhone} onChange={(v) => set({ employer: { ...ilr.employer, lineManagerPhone: v } })} />,

    'xilr.h.otherTraining': ({ children }) => <Fieldset legend={t('xilr.h.otherTraining.title')}>{children}</Fieldset>,
    'xilr.otherTraining': () => (
      <>
        <YesNoRadio legend={t('xilr.otherTraining.label')} name="ot-gov" error={yn('Other training in the last 12 months')} value={ilr.otherTraining.attended12m} onChange={(v) => set({ otherTraining: { ...ilr.otherTraining, attended12m: v } })} />
        {ilr.otherTraining.attended12m && (
          <LabeledInput label={t('xilr.otherTraining.when')} type="date" missingKey="Other training — when completed" value={ilr.otherTraining.completedWhen} onChange={(v) => set({ otherTraining: { ...ilr.otherTraining, completedWhen: v } })} />
        )}
      </>
    ),

    'xilr.h.circumstances': ({ children }) => <Fieldset legend={t('xilr.h.circumstances.title')}>{children}</Fieldset>,
    'xilr.caring': () => <LabeledTextarea label={t('xilr.caring.label')} missingKey="Caring responsibilities" value={ilr.circumstances.caringResponsibilities} onChange={(v) => set({ circumstances: { ...ilr.circumstances, caringResponsibilities: v } })} />,
    'xilr.otherCircumstances': () => <LabeledTextarea label={t('xilr.otherCircumstances.label')} missingKey="Other personal circumstances" value={ilr.circumstances.other} onChange={(v) => set({ circumstances: { ...ilr.circumstances, other: v } })} />,
    'xilr.careLeaver': () => <YesNoRadio legend={t('xilr.careLeaver.label')} name="pc-care" error={yn('Care leaver')} value={ilr.circumstances.careLeaver} onChange={(v) => set({ circumstances: { ...ilr.circumstances, careLeaver: v } })} />,

    'xilr.h.understanding': ({ children }) => <Fieldset legend={t('xilr.h.understanding.title')}>{children}</Fieldset>,
    'xilr.programmeUnderstanding': () => <LabeledTextarea label={t('xilr.programmeUnderstanding.label')} missingKey="Understanding of the programme" value={ilr.understanding.programmeUnderstanding} onChange={(v) => set({ understanding: { ...ilr.understanding, programmeUnderstanding: v } })} />,
    'xilr.careerProgression': () => <LabeledTextarea label={t('xilr.careerProgression.label')} rows={5} missingKey="Career progression" value={ilr.understanding.careerProgression} onChange={(v) => set({ understanding: { ...ilr.understanding, careerProgression: v } })} />,

    // Replaced the age questions, media consent, the learning declaration and
    // the provider declaration. Wording shared with the PDF (ilrAdditionalText).
    'xilr.h.additional': ({ children }) => <Fieldset legend={t('xilr.h.additional.title')}>{children}</Fieldset>,
    'xilr.jobRoleRelevance': ({ required }) => <LabeledTextarea label={t('xilr.jobRoleRelevance.label')} required={required} rows={4} missingKey="Job role and the programme" value={ai.jobRoleRelevance} onChange={(v) => setAi({ jobRoleRelevance: v })} />,
    'xilr.residenceNotFte': ({ required }) => <LabeledSelect label={t('xilr.residenceNotFte.label')} required={required} missingKey="Residence not for full-time education" value={ai.residenceNotForFullTimeEducation} options={YES_NO_SELECT} onChange={(v) => setAi({ residenceNotForFullTimeEducation: v })} />,
    'xilr.ehcp': ({ required }) => (
      <>
        <h4 className="text-[13px] font-heading font-semibold text-foreground-800 pt-4 pb-1">{t('xilr.ehcp.heading')}</h4>
        <p className="text-[12px] text-foreground-500 pb-2 leading-relaxed">{t('xilr.ehcp.sharing')}</p>
        <LabeledSelect label={t('xilr.ehcp.label')} required={required} missingKey="EHCP" value={ai.ehcp} options={YES_NO_SELECT} onChange={(v) => setAi({ ehcp: v })} />
        <p className="text-[12px] text-foreground-500 py-2 leading-relaxed">{t('xilr.ehcp.use')}</p>
      </>
    ),
    'xilr.over50Percent': () => <YesNoRadio legend={t('xilr.over50Percent.label')} name="d-50pc" error={yn('Declaration — over 50% in England')} value={ilr.declarations.over50PercentEngland} onChange={(v) => set({ declarations: { ...ilr.declarations, over50PercentEngland: v } })} />,
    'xilr.wageRate': ({ required }) => (
      <>
        <LabeledSelect label={t('xilr.wageRate.label')} required={required} missingKey="Wage rate band" value={ilr.declarations.wageRateBand} options={[...IlrText.wageRateOptions]} onChange={(v) => set({ declarations: { ...ilr.declarations, wageRateBand: v } })} />
        <p className="text-[12px] text-foreground-500 py-2 leading-relaxed">
          {t('xilr.wageRate.linkIntro')}{' '}
          <a href={IlrText.wageRateLink} target="_blank" rel="noreferrer" className="text-primary-600 hover:underline">
            {IlrText.wageRateLink}
          </a>
        </p>
      </>
    ),
    'xilr.knownByOtherName': () => (
      <>
        {/* Answering No clears the listed names, so a hidden answer is never saved or printed. */}
        <YesNoRadio legend={t('xilr.knownByOtherName.label')} name="d-othername" error={yn('Known by another name')} value={ilr.declarations.knownByOtherName} onChange={(v) => set({ declarations: { ...ilr.declarations, knownByOtherName: v }, ...(v ? {} : { additionalInformation: { ...ai, otherNames: '' } }) })} />
        {ilr.declarations.knownByOtherName === true && (
          <LabeledTextarea label={t('xilr.knownByOtherName.otherNames')} value={ai.otherNames} onChange={(v) => setAi({ otherNames: v })} />
        )}
      </>
    ),
    'xilr.plrAccessAware': () => (
      <>
        <p className="text-[12px] text-foreground-600 font-medium pt-3 pb-2 leading-relaxed">{t('xilr.plrAccessAware.record')}</p>
        <p className="text-[12px] text-foreground-500 pb-2 leading-relaxed">{t('xilr.plrAccessAware.sharing')}</p>
        <YesNoRadio legend={t('xilr.plrAccessAware.label')} name="d-plraccess" error={yn('Aware provider will access PLR')} value={ilr.declarations.plrAccessAware} onChange={(v) => set({ declarations: { ...ilr.declarations, plrAccessAware: v } })} />
      </>
    ),

    // Save + export. The document renders whatever is on screen, so it can
    // be produced before saving; saving persists to enrolment."Extended_ILR".
    'xilr.documentActions': () => (
      <>
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <button className={btnPrimary} onClick={() => downloadIlrDocument(ilr, board, layout.texts)}>
            <AppIcon className="ri-file-download-line" />Download ILR document
          </button>
          {/* A plain save is the wizard footer's job — Next on the learner side,
              "Save progress" on the staff side; this one additionally files the
              PDF into Compliance documents. */}
          <button className={btnSecondary} onClick={saveAndFile} disabled={ilrSaving || ilrFiling}>
            {ilrFiling ? <><AppIcon className="ri-loader-4-line animate-spin" />Filing…</> : <><AppIcon className="ri-folder-upload-line" />Save &amp; file document</>}
          </button>
          {ilrSavedAt && !ilrSaving && !ilrFiling && (
            <span className="text-[12px] text-emerald-600 inline-flex items-center gap-1">
              <AppIcon className="ri-check-line" />Saved
            </span>
          )}
        </div>
        <p className="text-[12px] text-foreground-500 mb-2">{t('xilr.documentActions.note')}</p>
      </>
    ),
  };

  return (
    <div>
      <StepHeading title={title} subtitle={t('step.ilr.subtitle')} />
      <div className="max-w-3xl">
        <StepItems slug="ilr" renderers={renderers} />
      </div>
    </div>
  );
}
