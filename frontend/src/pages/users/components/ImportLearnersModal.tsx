import { useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import {
  importEnrolmentUsers,
  LearnerImportError,
  LEARNER_IMPORT_MAX_BYTES,
  LEARNER_IMPORT_MAX_ROWS,
  type LearnerImportResult,
} from '@/api/enrolmentUserImport';
import type { UserListRow } from '../types';
import { DownloadLearnerTemplateButton } from './DownloadLearnerTemplateButton';
import { Modal } from './Modal';
import { btnPrimary, btnSecondary, inputClass } from './ui';

function accountWarning(row: UserListRow): string | null {
  const outcome = row.invitation;
  if (outcome?.forbidden) return outcome.error || 'Account provisioning was not permitted.';
  if (outcome && !outcome.accountCreated && !outcome.awaitingInvitation) {
    return outcome.error || 'The learner was saved, but their sign-in account could not be created.';
  }
  if (outcome && !outcome.awaitingInvitation && !outcome.emailSent) {
    return outcome.error || 'The invitation email could not be sent.';
  }
  if (row.hasAccount === false) return 'The learner was saved, but has no sign-in account yet.';
  return null;
}

export function ImportLearnersModal({ onClose, onImported }: {
  onClose: () => void;
  onImported: (rows: UserListRow[]) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<LearnerImportResult | null>(null);
  const [error, setError] = useState('');
  const [phase, setPhase] = useState<'select' | 'validating' | 'preview' | 'importing' | 'done'>('select');
  const busy = useRef(false);
  const pending = phase === 'validating' || phase === 'importing';
  const ready = phase === 'preview' && result && result.count > 0 && result.errors.length === 0;
  const warnings = phase === 'done' ? (result?.results ?? []).flatMap(row => {
    const message = accountWarning(row);
    return message ? [{ row, message }] : [];
  }) : [];

  const selectFile = (next: File | null) => {
    if (busy.current) return;
    setResult(null);
    setPhase('select');
    setError('');
    setFile(null);
    if (!next) return;
    if (!next.name.toLowerCase().endsWith('.xlsx')) {
      setError('Choose an Excel workbook (.xlsx) using the downloaded template.');
    } else if (next.size > LEARNER_IMPORT_MAX_BYTES) {
      setError('The file is too large. Choose a workbook no larger than 5 MB.');
    } else if (next.size === 0) {
      setError('The file is empty. Fill in the template and save it before uploading.');
    } else {
      setFile(next);
    }
  };

  const submit = async (dryRun: boolean) => {
    if (!file || busy.current || (!dryRun && !ready)) return;
    busy.current = true;
    setError('');
    setPhase(dryRun ? 'validating' : 'importing');
    try {
      const response = await importEnrolmentUsers(file, dryRun);
      setResult(response);
      if (dryRun) {
        setPhase('preview');
      } else {
        setPhase('done');
        onImported(response.results);
      }
    } catch (err) {
      setResult(err instanceof LearnerImportError ? err.result ?? null : null);
      setError(err instanceof Error ? err.message : 'Could not process the workbook.');
      // A failed or uncertain import always needs a fresh validation before retry.
      setPhase('select');
    } finally {
      busy.current = false;
    }
  };

  return (
    <Modal
      title="Import learners"
      size="max-w-4xl"
      dismissible={phase !== 'importing'}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={`${btnSecondary} disabled:cursor-not-allowed disabled:opacity-50`} onClick={onClose} disabled={phase === 'importing'}>
            {phase === 'done' ? 'Done' : 'Cancel'}
          </button>
          {phase !== 'done' && (
            ready || phase === 'importing'
              ? <button type="button" className={btnPrimary} disabled={pending} onClick={() => submit(false)}>
                  <AppIcon className={phase === 'importing' ? 'ri-loader-4-line animate-spin' : 'ri-user-add-line'} />
                  {phase === 'importing' ? 'Importing...' : `Import ${result?.count ?? 0} learners`}
                </button>
              : <button type="button" className={btnPrimary} disabled={!file || pending} onClick={() => submit(true)}>
                  <AppIcon className={phase === 'validating' ? 'ri-loader-4-line animate-spin' : 'ri-file-search-line'} />
                  {phase === 'validating' ? 'Validating...' : 'Validate file'}
                </button>
          )}
        </>
      }
    >
      <div className="space-y-5 text-[13px]">
        {phase !== 'done' && (
          <>
            <div className="rounded-xl border border-primary-100 bg-primary-50/50 p-4 text-foreground-700">
              <ol className="list-inside list-decimal space-y-1.5">
                <li>Download the template and fill in one learner per row.</li>
                <li>Keep the column headings and follow the instructions in the workbook.</li>
                <li>First name, Surname and Email are required for every learner.</li>
                <li>Upload the completed file, review validation, then import the learners.</li>
              </ol>
              <div className="mt-3"><DownloadLearnerTemplateButton /></div>
              <p className="mt-3 text-[12px] text-foreground-500">Importing creates learner records and prepares their accounts. Invitations are sent separately from Accounts when you are ready.</p>
            </div>
            <div>
              <label htmlFor="learner-import-file" className="mb-1.5 block font-medium text-foreground-800">Completed template</label>
              <input
                id="learner-import-file"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                disabled={pending}
                onChange={event => selectFile(event.target.files?.[0] ?? null)}
                className={`${inputClass} file:mr-3 file:rounded-md file:border-0 file:bg-primary-50 file:px-3 file:py-1 file:text-primary-700`}
                aria-describedby="learner-import-limits"
              />
              <p id="learner-import-limits" className="mt-1.5 text-[12px] text-foreground-500">Excel (.xlsx), up to 5 MB and {LEARNER_IMPORT_MAX_ROWS} learners. New learners only; existing email addresses are rejected.</p>
            </div>
          </>
        )}

        {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700">{error}</p>}

        {result && result.errors.length > 0 && (
          <section className="rounded-xl border border-red-200 bg-red-50 p-4">
            <h3 className="font-semibold text-red-800">{result.errors.length} issue{result.errors.length === 1 ? '' : 's'} found in {result.count} learner row{result.count === 1 ? '' : 's'}</h3>
            <p className="mt-1 text-red-700">No learners were imported. Correct the workbook and select the updated file.</p>
            <ul className="mt-3 max-h-52 space-y-1.5 overflow-y-auto text-red-700">
              {result.errors.map((issue, index) => <li key={`${issue.row}-${issue.field}-${index}`}>
                <strong>{issue.row > 0 ? `Row ${issue.row}` : 'File'}{issue.field ? ` (${issue.field})` : ''}:</strong> {issue.message}
              </li>)}
            </ul>
          </section>
        )}

        {ready && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-emerald-800">{result.count} learner{result.count === 1 ? '' : 's'} ready to import. No learners have been saved yet.</p>}
        {phase === 'importing' && <p role="status" className="text-foreground-600">Saving learners and preparing their accounts. Keep this page open until the import finishes.</p>}

        {phase === 'done' && result && (
          <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-800">
            <p className="font-semibold">{result.imported} learner{result.imported === 1 ? '' : 's'} imported successfully.</p>
            <p className="mt-1">The user directory has been updated. Send invitations from Accounts in the Super Admin workspace when the records are ready.</p>
          </div>
        )}

        {warnings.length > 0 && <section className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-800">
          <h3 className="font-semibold">Account setup needs attention</h3>
          <ul className="mt-2 space-y-1.5">{warnings.map(({ row, message }) => <li key={`${row.source}-${row.id}`}><strong>{row.email}:</strong> {message}</li>)}</ul>
        </section>}

        {result && result.preview.length > 0 && (
          <div className="max-h-72 overflow-auto rounded-xl border border-foreground-200">
            <table className="w-full text-left text-[12px]">
              <caption className="sr-only">Learners in the uploaded workbook</caption>
              <thead className="sticky top-0 bg-background-100 text-foreground-600"><tr>{['Row', 'Name', 'Email', 'Programme', 'Cohort', 'Group'].map(heading => <th key={heading} scope="col" className="whitespace-nowrap px-3 py-2.5 font-semibold">{heading}</th>)}</tr></thead>
              <tbody className="divide-y divide-foreground-100 text-foreground-700">
                {result.preview.map(row => <tr key={row.row}>
                  {[row.row, row.name, row.email, row.programme, row.cohort, row.group].map((value, index) => <td key={index} className="px-3 py-2.5 align-top">{value || '—'}</td>)}
                </tr>)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Modal>
  );
}
