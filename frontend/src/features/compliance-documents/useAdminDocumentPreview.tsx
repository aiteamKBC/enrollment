import { useEffect, useRef, useState } from 'react';
import { Eye } from 'lucide-react';
import { Modal } from '@/pages/users/components/Modal';
import { pendingSignaturesChanged } from '@/features/pending-signatures/events';
import { request } from './api';
import { DocumentPreview } from './DocumentPreview';
import type { DocumentKey, PreviewResponse } from './types';

const path = (learner: string, key: DocumentKey) => `/enrolment_api/document-templates/learners/${encodeURIComponent(learner)}/documents/${key}`;
type Review = { key: DocumentKey; source?: string; value: PreviewResponse; proceed?: (value: PreviewResponse) => void };

/** Shared by all eight admin rows; never calls Issue, Edit or review-form GET. */
export function useAdminDocumentPreview(learnerId: string) {
  const [preview, setPreview] = useState<Review | null>(null);
  const [reviewed, setReviewed] = useState<Review | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [failed, setFailed] = useState(false);
  const sequence = useRef({ generation: 0 });
  const close = () => { sequence.current.generation++; setLoading(false); setFailed(false); setPreview(null); };
  useEffect(() => {
    const tracker = sequence.current;
    tracker.generation++; setLoading(false); setFailed(false); setPreview(null); setReviewed(null);
    return () => { tracker.generation++; };
  }, [learnerId]);
  const open = async (key: DocumentKey, source?: string, proceed?: Review['proceed']) => {
    const current = ++sequence.current.generation;
    setLoading(true); setError(''); setFailed(false); setPreview(null); setReviewed(null);
    try {
      const query = source ? `sourceId=${encodeURIComponent(source)}` : 'phase=pre-issue';
      const value = await request<PreviewResponse>(`${path(learnerId, key)}/preview/?${query}`);
      if (current === sequence.current.generation) setPreview({ key, source, value, proceed });
    } catch (e) { if (current === sequence.current.generation) { setError(e instanceof Error ? e.message : 'Could not open this document.'); setFailed(true); } }
    finally { if (current === sequence.current.generation) setLoading(false); }
  };
  const sign = async <T,>(key: DocumentKey, source: string, name: string, signature: string): Promise<T> => {
    if (!reviewed || reviewed.key !== key || reviewed.source !== source || !reviewed.value.previewToken) {
      throw new Error('Preview this issued document again before signing.');
    }
    const result = await request<T>(`${path(learnerId, key)}/sign-reviewed/`, 'POST', {
      sourceId: source, reviewToken: reviewed.value.previewToken,
      party: key.startsWith('review-') ? 'admin' : 'provider', name, signature,
    });
    setReviewed(null); pendingSignaturesChanged();
    return result;
  };
  const button = (key: DocumentKey, source?: string) => <button type="button" disabled={loading}
    className="inline-flex items-center gap-1 rounded-md border border-primary-100 bg-primary-50 px-2 py-1 text-[11px] font-semibold text-primary-700 hover:bg-primary-100 disabled:opacity-60"
    aria-label={`Preview ${key.replaceAll('-', ' ')}`} onClick={() => { void open(key, source); }}>
    <Eye size={12} aria-hidden="true" />Preview</button>;
  const dialog = <>
    {(loading || failed) && <Modal title="Document preview" onClose={close}>
      {loading ? <p role="status">Loading the document…</p> : <p role="alert">{error}</p>}
    </Modal>}
    {preview && <DocumentPreview value={preview.value} onClose={close}
      onContinue={preview.proceed ? () => { setReviewed(preview); setPreview(null); preview.proceed?.(preview.value); } : undefined} />}
  </>;
  return { open, sign, button, dialog };
}
