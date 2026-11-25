import { useEffect, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { previewPlrPdf, type PlrPdfPreviewResult, type PlrPdfImportResult } from '@/api/plrPdfPreview';
import type { LearnerKind } from '@/api/extendedIlr';
import { Modal } from '../../components/Modal';
import { Table, Pagination, btnSecondary, btnPrimary } from '../../components/ui';

const PAGE_SIZE = 10;
export function PlrPdfPreview({ kind, learnerId, enteredUln, onImport }: { kind: LearnerKind; learnerId: string; enteredUln: string;
  onImport?: (file: File, enteredUln: string, signal: AbortSignal) => Promise<PlrPdfImportResult> }) {
  const input = useRef<HTMLInputElement>(null);
  const active = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const originalFile = useRef<File | null>(null);
  const target = useRef(`${kind}/${learnerId}`);
  target.current = `${kind}/${learnerId}`;
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<PlrPdfPreviewResult | null>(null);
  const [page, setPage] = useState(1);
  useEffect(() => { setMessage(''); }, [kind, learnerId]);
  useEffect(() => {
    sequence.current += 1;
    active.current?.abort(); originalFile.current = null; setPreview(null); setError(''); setBusy(false); setImporting(false);
    return () => { sequence.current += 1; active.current?.abort(); originalFile.current = null; };
  }, [kind, learnerId, enteredUln]);

  const select = async (file: File) => {
    setError(''); setMessage(''); setPreview(null); originalFile.current = null;
    if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && !['application/pdf', 'application/octet-stream'].includes(file.type)) || !file.size || file.size > 8 * 1024 * 1024) {
      setError('Select a PDF file no larger than 8 MB.'); return;
    }
    if (enteredUln && !/^[0-9]{10}$/.test(enteredUln)) { setError('The entered ULN must be empty or contain exactly 10 digits.'); return; }
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const request = ++sequence.current;
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    setBusy(true);
    try {
      const result = await previewPlrPdf(kind, learnerId, file, enteredUln, controller.signal);
      if (request === sequence.current) { originalFile.current = file; setPreview(result); setPage(1); }
    } catch (e) {
      if (request === sequence.current) setError(controller.signal.aborted ? 'The PDF preview timed out. Please retry.'
        : e instanceof TypeError ? 'The PDF preview service could not be reached. Please retry.'
          : e instanceof Error ? e.message : 'The PDF could not be previewed. Please retry.');
    } finally {
      window.clearTimeout(timeout);
      if (request === sequence.current) setBusy(false);
    }
  };
  const confirm = async () => {
    const file = originalFile.current;
    if (!file || !preview?.identityVerified || importing || !onImport) return;
    const request = sequence.current;
    const selectedTarget = target.current;
    const controller = new AbortController(); active.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    setImporting(true); setError('');
    try {
      const result = await onImport(file, enteredUln, controller.signal);
      if (target.current === selectedTarget) { setMessage(result.message); setPreview(null); originalFile.current = null; }
    } catch (e) {
      if (request === sequence.current) setError(controller.signal.aborted || e instanceof TypeError
        ? 'The import result could not be confirmed. Reload PLR to check saved records before retrying.'
        : e instanceof Error ? e.message : 'The PLR could not be imported. Please retry.');
    } finally {
      window.clearTimeout(timeout);
      if (request === sequence.current) setImporting(false);
    }
  };
  const rows = preview?.records.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) ?? [];
  return <>
    <button type="button" className={btnSecondary} disabled={busy || importing} aria-busy={busy || importing} onClick={() => input.current?.click()}>
      <AppIcon className={busy ? 'ri-loader-4-line animate-spin' : 'ri-upload-2-line'} />{busy ? 'Processing PDF…' : 'Import PLR PDF'}
    </button>
    <input ref={input} className="sr-only" type="file" accept=".pdf,application/pdf" aria-label="Select PLR PDF" disabled={busy || importing}
      onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void select(file); }} />
    {message && <p role="status" className="w-full text-sm text-green-700">{message}</p>}
    {error && !preview && <p role="alert" className="w-full text-sm text-red-600">{error}</p>}
    {preview && <Modal title="PLR PDF preview" size="max-w-6xl" onClose={() => { if (!importing) { setPreview(null); originalFile.current = null; } }}
      footer={<div className="flex justify-end gap-2">
        <button type="button" className={btnSecondary} disabled={importing} onClick={() => { setPreview(null); originalFile.current = null; }}>Close preview</button>
        <button type="button" className={btnPrimary} disabled={importing || !preview.identityVerified || !onImport} onClick={() => void confirm()}>{importing ? 'Importing…' : 'Import Records'}</button>
      </div>}>
      <p className="mb-4 text-sm font-medium">Nothing has been saved yet. Import Records will save the ULN and qualifications for this student.</p>
      {!preview.identityVerified && <p className="mb-3 text-sm text-amber-700">Import requires a verified learner name and date of birth.</p>}
      {error && <p role="alert" className="mb-3 text-sm text-red-600">{error}</p>}
      <dl className="mb-4 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
        <div><dt className="font-medium">Extracted ULN</dt><dd>{preview.uln}</dd></div>
        <div><dt className="font-medium">Learner name</dt><dd>{preview.learnerName}</dd></div>
        <div><dt className="font-medium">Date of birth</dt><dd>{preview.dateOfBirth}</dd></div>
        <div><dt className="font-medium">Learning Events count</dt><dd>{preview.counts.learningEvents}</dd></div>
        <div><dt className="font-medium">Achievements count</dt><dd>{preview.counts.achievements}</dd></div>
      </dl>
      {preview.warnings.length > 0 && <ul aria-label="PDF validation warnings" className="mb-4 list-disc pl-5 text-sm text-amber-700">
        {preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
      </ul>}
      <Table headers={['Section', 'Place of Study', 'Awarding Body', 'Qualification Type', 'Subject', 'Reference Number', 'Level', 'Start Date', 'End Date', 'Award Date', 'Credits', 'Grade', 'Completion Status', 'Outcome Status', 'Assessment Language', 'Record Type']}>
        {rows.length === 0 ? <tr><td colSpan={16} className="px-3 py-4 text-center text-sm">The report contains no qualification records.</td></tr>
          : rows.map((row, index) => <tr key={`${page}-${index}`} className="border-b border-foreground-100 align-top">
            {[row.sourceSection, row.placeOfStudy, row.awardingBody, row.qualificationType, row.subject, row.referenceNumber,
              row.level, row.startDate, row.endDate, row.awardDate, row.credits, row.grade, row.completionStatus,
              row.outcomeStatus, row.language, row.recordType].map((value, column) => <td key={column} className="px-3 py-2 text-[12px] text-foreground-700">{value}</td>)}
          </tr>)}
      </Table>
      <Pagination page={page} totalPages={Math.max(1, Math.ceil(preview.records.length / PAGE_SIZE))} onChange={setPage} />
    </Modal>}
  </>;
}
