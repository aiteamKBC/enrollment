import type { PersonalDetails } from '../types';
import { textFor } from './layout/texts';

export const PAPERWORK_SIGNATURE_DECLARATION = 'By signing, I confirm the ILR accuracy declaration and authorise this signature to be used on my enrolment paperwork.';

/** Save the wording agreed to, separately from the original image/signing date. */
export function signatureDeclaration(texts?: Record<string, string>): string {
  return `${textFor(texts, 'ild.signature.certify')}\n${PAPERWORK_SIGNATURE_DECLARATION}`;
}

export function hasConfirmedSignature(pd: PersonalDetails, texts?: Record<string, string>): boolean {
  return Boolean(pd.signature?.trim() && pd.signatureDeclaration?.text === signatureDeclaration(texts)
    && pd.signatureDeclaration.confirmedAt);
}
