import { ageFromDob, useWizard } from "../WizardContext.helpers";
import { SEX_OPTIONS } from '@/mocks/enrolment-console';
import { LabeledInput, LabeledSelect, SignatureField, StepHeading } from './fields';
import { StepItems, type ItemRenderer } from '../layout/StepItems';
import { useStepTitle } from '../layout/useLayout';
import { useText } from '../layout/textsContext';

export default function PersonalDetails() {
  const { draft, setSection } = useWizard();
  const pd = draft.personalDetails;
  const set = (patch: Partial<typeof pd>) => setSection('personalDetails', { ...pd, ...patch });
  const title = useStepTitle('personal-details', 'Personal Details');
  const t = useText();

  // One renderer per item in layout/registry.ts; the layout decides order and
  // which are shown.
  const renderers: Record<string, ItemRenderer> = {
    'pd.firstName': () => <LabeledInput label={t('pd.firstName.label')} missingKey="First Name" value={pd.firstName} onChange={(v) => set({ firstName: v })} />,
    'pd.lastName': () => <LabeledInput label={t('pd.lastName.label')} missingKey="Last Name" value={pd.lastName} onChange={(v) => set({ lastName: v })} />,
    'pd.email': () => <LabeledInput label={t('pd.email.label')} missingKey="Email" type="email" value={pd.email} onChange={(v) => set({ email: v })} />,
    'pd.phone': () => <LabeledInput label={t('pd.phone.label')} missingKey="Phone" type="tel" value={pd.phone} onChange={(v) => set({ phone: v })} />,
    'pd.address': () => <LabeledInput label={t('pd.address.label')} missingKey="Address" value={pd.address} onChange={(v) => set({ address: v })} />,
    // Age is derived from the date of birth, never typed — the two can't
    // disagree, and there is nothing to keep in step by hand.
    'pd.dob': () => (
      <>
        <LabeledInput label={t('pd.dob.label')} missingKey="Date of Birth" type="date" value={pd.dob} onChange={(v) => set({ dob: v, age: ageFromDob(v) })} />
        <LabeledInput
          label={t('pd.dob.ageLabel')}
          type="number"
          readOnly
          value={pd.age != null ? String(pd.age) : ''}
          onChange={() => {}}
          placeholder="—"
          helper={t('pd.dob.ageHelp')}
        />
      </>
    ),
    'pd.sex': () => <LabeledSelect label={t('pd.sex.label')} missingKey="Sex" value={pd.sex} options={SEX_OPTIONS} onChange={(v) => set({ sex: v })} />,
    // Signature — the learner's own name in a script face; stored on
    // Wizard_Personal_Details.
    'pd.signature': () => (
      <div className="pt-2 border-t border-foreground-100 mt-2">
        <SignatureField
          label={t('pd.signature.label')}
          missingKey="Your signature"
          signatoryName={[pd.firstName, pd.lastName].filter(Boolean).join(' ')}
          value={pd.signature}
          onChange={(v) =>
            set({
              signature: v || undefined,
              // Stamp the date on signing, clear it when the signature is removed.
              signatureDate: v ? pd.signatureDate || new Date().toISOString().slice(0, 10) : undefined,
            })
          }
        />
        <p className="text-[12px] text-foreground-500">{t('pd.signature.help')}</p>
      </div>
    ),
  };

  return (
    <div>
      <StepHeading title={title} />
      <div className="max-w-3xl">
        <StepItems slug="personal-details" renderers={renderers} />
      </div>
    </div>
  );
}
