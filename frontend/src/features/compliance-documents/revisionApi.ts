import { pendingSignaturesChanged } from '@/features/pending-signatures/events';
import { request } from './api';
import type { DocumentKey, DocumentTemplate, PreviewResponse } from './types';

export interface DocumentRevision {
  id: string; issueId: string; sourceId: string; documentKey: DocumentKey; title: string; number: number;
  baseId: string | null; state: 'draft' | 'original' | 'finalized'; editVersion: number;
  signatureMethod: '' | 'original' | 'keep' | 'sign-again'; status: string; fullySigned: boolean;
  templateVersionId: string; templateVersionNumber: number; createdBy: string; createdAt: string;
  finalizedAt: string | null; reason: string; changes: Record<string, unknown>; checksum: string;
  canDiscard?: boolean;
  creationMode?: 'blank' | 'template' | 'learner' | 'edit';
  provenance: Record<string, { sourceRevision: number; name: string; signedAt: string | null }>;
  signatures: Record<string, { signed: boolean; name: string; signedAt: string | null }>;
}
export interface RevisionDetail { revision: DocumentRevision; preview: PreviewResponse }
export interface RevisionStorageSetup { code: string; message: string; missingTables: string[]; disabledSettings: string[]; checkCommand: string; planCommand: string; setupCommand: string }
const root = (learnerId: string) => `/enrolment_api/document-revisions/learners/${encodeURIComponent(learnerId)}`;
const item = (learnerId: string, id: string) => `${root(learnerId)}/revisions/${encodeURIComponent(id)}/`;
export const revisionHistory = (learnerId: string, key: DocumentKey) => request<{ results: DocumentRevision[]; setupRequired: boolean; setup?: RevisionStorageSetup }>(`${root(learnerId)}/${key}/history/`);
export const startRevision = (learnerId: string, key: DocumentKey, sourceId: string, baseRevisionId?: string) => request<RevisionDetail>(`${root(learnerId)}/${key}/drafts/`, 'POST', { sourceId, baseRevisionId });
export const createDocumentFromTemplate = (learnerId: string, key: DocumentKey, sourceId: string, requestId: string) => request<RevisionDetail>(`${root(learnerId)}/${key}/drafts/`, 'POST', { sourceId, mode: 'learner', requestId });
export const readRevision = (learnerId: string, id: string) => request<RevisionDetail>(item(learnerId, id));
export const discardRevision = (learnerId: string, revision: DocumentRevision) =>
  request<{ discarded: boolean; alreadyDiscarded: boolean; id: string }>(`${root(learnerId)}/${revision.documentKey}/revisions/${encodeURIComponent(revision.id)}/`, 'DELETE', { sourceId: revision.sourceId, editVersion: revision.editVersion });
export const saveRevision = (learnerId: string, id: string, editVersion: number, content: DocumentTemplate, reason: string, signatureMethod: string) => request<RevisionDetail>(item(learnerId, id), 'PUT', { editVersion, content, reason, signatureMethod });
export const previewRevision = (learnerId: string, id: string, editVersion: number) => request<PreviewResponse>(`${item(learnerId, id)}preview/`, 'POST', { editVersion });
export const finalizeRevision = (learnerId: string, id: string, previewToken: string) => request<RevisionDetail>(`${item(learnerId, id)}finalize/`, 'POST', { previewToken }).then((value) => { pendingSignaturesChanged(); return value; });
export const signRevision = (learnerId: string, id: string, party: string, name: string, signature: string) => request<RevisionDetail>(`${item(learnerId, id)}sign/`, 'POST', { party, name, signature }).then((value) => { pendingSignaturesChanged(); return value; });
export const REVIEW_DOCUMENT_KEYS: Record<string, DocumentKey> = { 'eligibility-review': 'review-eligibility', workspace: 'review-rpl', 'training-plan': 'review-health-safety', 'uln-privacy': 'review-uln' };
