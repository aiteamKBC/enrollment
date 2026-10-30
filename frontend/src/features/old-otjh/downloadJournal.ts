import { getMonth, type MonthDetail, type JournalSummary } from './api';
import { monthLabel } from './report';
import type { JournalPdfAssets, PdfImage } from './journalPdf';

async function loadImage(url: string, signal?: AbortSignal): Promise<PdfImage> {
  const response = await fetch(url, { credentials: 'same-origin', signal });
  if (!response.ok) throw new Error('The report logo or a saved signature could not be loaded. Please try again.');
  const type = response.headers.get('content-type')?.split(';')[0];
  const data = new Uint8Array(await response.arrayBuffer());
  const png = data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47;
  const jpeg = data[0] === 0xff && data[1] === 0xd8;
  if ((!png && !jpeg) || (type && !type.startsWith('image/') && type !== 'application/octet-stream')) {
    throw new Error('A report image is unavailable or invalid. Please try again.');
  }
  return { data, format: png ? 'PNG' : 'JPEG' };
}

export async function downloadJournal({ summary, months, aptemId, signal, onProgress, loadMonth }: {
  summary: JournalSummary; months: string[]; aptemId?: number; signal?: AbortSignal;
  loadMonth?: (month: string, signal?: AbortSignal) => Promise<MonthDetail>;
  onProgress?: (message: string) => void;
}) {
  if (!summary.learner || !months.length) throw new Error('No learner reports are available to download.');
  const selected = [...new Set(months)].sort();
  const available = new Set(summary.months.map(item => item.month));
  if (selected.some(month => !available.has(month))) throw new Error('A selected month is no longer available. Refresh the record and try again.');
  const reports: MonthDetail[] = [];
  // Fetch a bounded batch at a time so large previous records do not flood the
  // existing API. Do not download a partial document when any month fails.
  for (let index = 0; index < selected.length; index += 3) {
    signal?.throwIfAborted();
    onProgress?.(`Preparing reports ${index + 1}-${Math.min(index + 3, selected.length)} of ${selected.length}…`);
    const batch = await Promise.all(selected.slice(index, index + 3).map(async month => {
      const report = await (loadMonth ? loadMonth(month, signal) : getMonth(month, aptemId, signal));
      if (report.month !== month) throw new Error(`Could not load ${monthLabel(month)}. Please try again.`);
      return report;
    }));
    reports.push(...batch);
  }
  signal?.throwIfAborted();
  onProgress?.('Preparing PDF and saved signatures…');
  const signatureUrls = [...new Set(reports.flatMap(report => [report.student_signature?.url, report.coach_signature?.url]).filter((url): url is string => Boolean(url)))];
  const assets: JournalPdfAssets = { logo: await loadImage(`${import.meta.env.BASE_URL}assets/kbc-logo.png`, signal), signatures: new Map() };
  for (let index = 0; index < signatureUrls.length; index += 3) {
    const batch = await Promise.all(signatureUrls.slice(index, index + 3).map(async url => [url, await loadImage(url, signal)] as const));
    batch.forEach(([url, asset]) => assets.signatures.set(url, asset));
  }
  signal?.throwIfAborted();
  const { buildJournalPdf } = await import('./journalPdf');
  signal?.throwIfAborted();
  const doc = buildJournalPdf(summary, reports, assets);
  const name = summary.learner.name.replace(/[<>:"/\\|?*]/g, '-').replace(/\p{Cc}/gu, '-').trim() || 'Learner';
  const period = selected.length === 1 ? selected[0] : `all-months_${selected[0]}_to_${selected.at(-1)}`;
  await doc.save(`Learner-Journal_${name}_${period}.pdf`, { returnPromise: true });
}
