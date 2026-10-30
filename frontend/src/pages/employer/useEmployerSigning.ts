import { useEffect, useState } from 'react';
import {
  fetchEmployerReviewInstance,
  saveEmployerReviewAnswers,
  signAgreementAsEmployer,
  signDocumentAsEmployer,
  signEnrolmentReviewAsEmployer,
  signReviewAsEmployer,
  signTrainingPlanAsEmployer,
  signWrittenAgreementAsEmployer,
  type SignableItem,
} from '@/api/employerPortal';
import { fetchMigratedReviewForParty, signMigratedReviewAsParty } from '@/api/learnerCalendar';
import type { LearnerKind } from '@/api/extendedIlr';
import { useToast } from "@/hooks/useToast.helpers";

/** One signable item and the learner it belongs to. */
export type SigningTarget = { item: SignableItem; kind: LearnerKind; learnerId: string };

/** The same document counts and filters in All documents and a learner's tab. */
export function documentState(item: SignableItem): 'signed' | 'to-sign' | 'waiting' {
  if (item.signed) return 'signed';
  return item.signable ? 'to-sign' : 'waiting';
}

/** Reviews are keyed by event key and documents by id; the learner keeps keys unique across learners. */
export function targetKey({ item, kind, learnerId }: SigningTarget) {
  return `${kind}:${learnerId}:${item.kind === 'review' ? `r-${item.eventKey}` : `${item.kind}-${item.id}`}`;
}

/** Unsigned first, then signable-but-unsigned before blocked ones, so the row an employer can act on leads. */
export function compareSignable(a: SignableItem, b: SignableItem) {
  if (a.signed !== b.signed) return a.signed ? 1 : -1;
  if (a.signable !== b.signable) return a.signable ? -1 : 1;
  return a.label.localeCompare(b.label);
}

/**
 * Signing and opening an employer's documents, for any of their learners.
 *
 * `onSigned` runs after a signature is saved or withdrawn, so the caller can
 * reload the list it came from.
 */
export function useEmployerSigning(employerId: string, onSigned: () => void) {
  const { success } = useToast();
  const [signing, setSigning] = useState<SigningTarget | null>(null);
  const [reviewDefinition, setReviewDefinition] = useState<any>(null);
  const [loadedReviewKey, setLoadedReviewKey] = useState<string | null>(null);
  const [preview, setPreview] = useState<SigningTarget | null>(null);

  useEffect(() => {
    setReviewDefinition(null);
    setLoadedReviewKey(null);
    const item = signing?.item;
    if (!signing || item?.kind !== 'review' || !item.reviewInstanceId) {
      setReviewDefinition(null);
      return;
    }
    let active = true;
    (item.migratedForm
      ? fetchMigratedReviewForParty(item.eventKey)
      : fetchEmployerReviewInstance(employerId, signing.kind, signing.learnerId, item.eventKey))
      .then(value => {
        if (active) {
          setReviewDefinition(value);
          setLoadedReviewKey(targetKey(signing));
        }
      })
      .catch(() => { if (active) setReviewDefinition(null); });
    return () => { active = false; };
  }, [employerId, signing]);

  const sign = async (name: string, signature: string) => {
    if (!signing) return;
    const { item, kind, learnerId } = signing;
    if (item.kind === 'review') {
      // Curriculum review instances and legacy enrolment reviews share this
      // list but not a sign endpoint.
      if (item.migratedForm) {
        if (!reviewDefinition || loadedReviewKey !== targetKey(signing)) {
          throw new Error('Wait for the current review to load before signing.');
        }
        await signMigratedReviewAsParty(item.eventKey, signature, { reviewRevision: reviewDefinition.reviewRevision });
      } else if (item.reviewInstanceId) {
        await signReviewAsEmployer(employerId, kind, learnerId, item.eventKey, { name, signature });
      } else {
        await signEnrolmentReviewAsEmployer(kind, learnerId, item.eventKey, { name, signature });
      }
    } else if (item.kind === 'written-agreement') {
      await signWrittenAgreementAsEmployer(learnerId, { name, signature });
    } else if (item.kind === 'training-plan') {
      await signTrainingPlanAsEmployer(learnerId, { name, signature });
    } else if (item.kind === 'agreement') {
      // Its own table, its own endpoint — see apprenticeship_agreement.py.
      await signAgreementAsEmployer(learnerId, { name, signature });
    } else {
      await signDocumentAsEmployer(kind, learnerId, item.id, { name, signature });
    }
    // No reusable copy is kept: the employer's name always produces the same
    // mark, so there is nothing to save and nothing to go stale.
    success(signature ? 'Signed' : 'Signature removed', item.label);
    onSigned();
  };

  const signingItem = signing?.item;
  const saveReviewAnswers = signing && signingItem?.kind === 'review' && !signingItem.migratedForm
    ? (answers: Record<string, unknown>) =>
        saveEmployerReviewAnswers(employerId, signing.kind, signing.learnerId, signingItem.eventKey, answers)
    : undefined;

  return {
    signing,
    startSigning: setSigning,
    closeSigning: () => setSigning(null),
    reviewDefinition,
    preview,
    closePreview: () => setPreview(null),
    opening: preview ? targetKey(preview) : null,
    sign,
    openDocument: setPreview,
    saveReviewAnswers,
  };
}
