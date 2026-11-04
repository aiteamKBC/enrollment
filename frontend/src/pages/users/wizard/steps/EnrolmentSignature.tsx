import { useWizard } from '../WizardContext.helpers';
import { useText } from '../layout/textsContext';
import { SignatureField } from './fields';
import { hasConfirmedSignature, PAPERWORK_SIGNATURE_DECLARATION, signatureDeclaration } from '../signatureDeclaration';
import { FieldError } from '../stepErrors';
import { useMissing } from '../stepErrors.helpers';

/** One apprenticeship signing action; commercial paperwork wording is unchanged. */
export default function EnrolmentSignature() {
  const { draft, setSection, board, isCommercial, layout, ilrFinalisedAt, ilrLegacyReadOnly, readOnly } = useWizard();
  const locked = Boolean((ilrFinalisedAt || ilrLegacyReadOnly) && !isCommercial && !readOnly);
  const pd = draft.personalDetails;
  const t = useText();
  const missing = useMissing('Your signature');
  const confirmed = hasConfirmedSignature(pd, layout?.texts);
  const confirm = () => setSection('personalDetails', { ...pd, signatureDeclaration: {
    text: signatureDeclaration(layout?.texts), confirmedAt: new Date().toISOString(),
  } });
  return <section aria-label="Enrolment paperwork signature" className="pt-2 border-t border-foreground-100 mt-2">
    {!isCommercial && <p className="text-[13px] text-foreground-800">{PAPERWORK_SIGNATURE_DECLARATION}</p>}
    <SignatureField label={t('pd.signature.label')} missingKey="Your signature"
      readOnly={locked}
      missingBeforeSign={!isCommercial && pd.title === '' ? ['Title'] : []}
      signatoryName={[pd.firstName, pd.lastName].filter(Boolean).join(' ') || board?.user?.name || ''}
      value={pd.signature}
      onChange={value => setSection('personalDetails', { ...pd, signature: value,
        // An explicit empty string survives JSON and must not be refilled on refresh.
        signatureDate: value ? pd.signatureDate || new Date().toISOString().slice(0, 10) : '',
        ...(!isCommercial ? { signatureDeclaration: value ? {
          text: signatureDeclaration(layout?.texts), confirmedAt: new Date().toISOString(),
        } : null } : {}),
      })} />
    <p className="text-[12px] text-foreground-500">{t('pd.signature.help')}</p>
    {ilrFinalisedAt && locked && <p className="text-[12px] text-foreground-600">Signed {new Date(ilrFinalisedAt).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}</p>}
    {!locked && !isCommercial && pd.signature && !confirmed && <div className="mt-2 text-[12px] text-foreground-700">
      <p>Your saved signature and original signing date have been retained. Confirm below to apply this signature to the ILR accuracy declaration and enrolment paperwork shown here.</p>
      <button type="button" onClick={confirm} className="mt-2 text-primary-600 underline">Confirm signature for this declaration</button>
      {missing && <FieldError message="Please confirm your signature for this declaration, or sign again." />}
    </div>}
  </section>;
}
