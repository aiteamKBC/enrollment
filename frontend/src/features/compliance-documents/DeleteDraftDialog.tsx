import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { useToastOptional } from '@/hooks/useToast.helpers';
import { Modal } from '@/pages/users/components/Modal';
import { btnSecondary } from '@/pages/users/components/ui';
import { discardRevision, type DocumentRevision } from './revisionApi';

export const deleteDraftClass = `${btnSecondary} !border-red-200 !text-red-700 hover:!bg-red-50`;

export function DeleteDraftDialog({ learnerId, revision, onCancel, onDeleted }: {
  learnerId: string; revision: DocumentRevision; onCancel: () => void; onDeleted: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const toast = useToastOptional();
  const confirm = async () => {
    setBusy(true); setError('');
    try {
      await discardRevision(learnerId, revision);
      await onDeleted();
      toast?.success('Draft revision deleted');
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  };
  return <Modal title="Delete Draft Revision?" size="max-w-lg" dismissible={!busy} onClose={onCancel} footer={<>
    <button className={btnSecondary} disabled={busy} onClick={onCancel}>Cancel</button>
    <button className={deleteDraftClass} disabled={busy} onClick={() => void confirm()}><Trash2 size={14} aria-hidden="true" />{busy ? 'Deleting…' : 'Delete Draft'}</button>
  </>}>
    <p className="text-sm">Are you sure you want to delete Version {revision.number} — Draft? This will discard the draft revision. The original document, previous versions and signatures will remain unchanged.</p>
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
  </Modal>;
}
