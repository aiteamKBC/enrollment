import { useRef, useState } from 'react';
import { Download, Upload } from 'lucide-react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import type { Ksb } from './catalogApi';
import { downloadKsbCsv, makeKsbCsv, parseKsbCsv, type KsbFields } from './ksbCsv';
import { catalogueFocus } from './catalogControls';

export function KsbLibraryCsv({ programmeCode, rows, onImport, readOnly, preview }: {
  programmeCode: string;
  rows: Ksb[];
  onImport: (rows: KsbFields[]) => Promise<void>;
  readOnly: boolean;
  preview: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useRef<HTMLButtonElement>(null);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<{ name: string; rows: KsbFields[] }>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const secondary = `${btnSecondary} ${catalogueFocus}`;

  function download() {
    const csv = makeKsbCsv(rows.filter(row => !row.archived_at));
    downloadKsbCsv(programmeCode, 'draft', csv);
  }

  async function read(file: File) {
    setError('');
    setNotice('');
    setDraft(undefined);
    setReading(true);
    try {
      setDraft({ name: file.name, rows: parseKsbCsv(await file.text(), rows) });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not read the CSV file.');
    } finally {
      setReading(false);
    }
  }

  async function confirmImport() {
    if (!draft || saving) return;
    setError('');
    setSaving(true);
    try {
      await onImport(draft.rows);
      if (preview) setNotice(`${draft.rows.length} ${draft.rows.length === 1 ? 'KSB' : 'KSBs'} imported into preview.`);
      setDraft(undefined);
      upload.current?.focus();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not import the CSV.');
    } finally {
      setSaving(false);
    }
  }

  return <div className="space-y-2 text-sm">
    <div className="flex flex-wrap gap-2">
      <button type="button" className={secondary} onClick={download}>
        <Download size={16} aria-hidden /> Download draft CSV
      </button>
      {!readOnly && <>
        <input ref={input} type="file" accept=".csv,text/csv" hidden aria-label="KSB CSV file"
          onChange={event => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = '';
            if (file) void read(file);
          }} />
        <button ref={upload} type="button" aria-describedby="ksb-csv-help" className={`${secondary} disabled:opacity-50`}
          disabled={reading || saving} onClick={() => input.current?.click()}>
          <Upload size={16} aria-hidden /> Upload CSV
        </button>
      </>}
    </div>
    {!readOnly && <p id="ksb-csv-help" className="text-foreground-600">
      Use the code and description columns to add new KSBs to this {preview ? 'preview' : 'programme'}.
    </p>}
    {reading && <p role="status">Reading CSV…</p>}
    {error && <p role="alert" className="break-words text-red-800">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {draft && !readOnly && <div className="space-y-2 rounded-lg border border-background-200 bg-background-50 p-3">
      <p className="break-words"><strong>{draft.name}</strong> contains {draft.rows.length} {draft.rows.length === 1 ? 'new KSB' : 'new KSBs'}.</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={`${btnPrimary} ${catalogueFocus}`} disabled={saving} onClick={() => void confirmImport()}>
          {saving ? 'Importing…' : 'Import KSBs'}
        </button>
        <button type="button" className={secondary} disabled={saving} onClick={() => {
          setDraft(undefined);
          upload.current?.focus();
        }}>Cancel import</button>
      </div>
    </div>}
  </div>;
}
