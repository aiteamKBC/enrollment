import { useEffect, useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import type { JournalSummary, MonthDetail } from './api';
import { downloadJournal } from './downloadJournal';
import styles from './journal.module.css';

export function JournalDownloads({ summary, month, aptemId, disabled, loadMonth }: {
  summary: JournalSummary; month: string; aptemId?: number; disabled: boolean;
  loadMonth?: (month: string, signal?: AbortSignal) => Promise<MonthDetail>;
}) {
  const [preparing, setPreparing] = useState<'month' | 'all' | null>(null);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    setPreparing(null); setProgress(''); setError('');
    return () => { request.current?.abort(); request.current = null; };
  }, [summary.learner?.id, summary.learner?.aptem_id]);

  const download = async (scope: 'month' | 'all') => {
    if (request.current || disabled) return;
    const controller = new AbortController();
    request.current = controller;
    setPreparing(scope); setError('');
    try {
      await downloadJournal({ summary, aptemId, loadMonth, months: scope === 'all' ? summary.months.map(item => item.month) : [month],
        signal: controller.signal, onProgress: message => { if (!controller.signal.aborted) setProgress(message); } });
      if (!controller.signal.aborted) setProgress('Your PDF is ready.');
    } catch (cause) {
      if (!controller.signal.aborted) {
        setProgress('');
        setError(cause instanceof Error ? cause.message : 'Could not prepare the PDF. Please try again.');
      }
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setPreparing(null);
    }
  };

  return <div className={styles.downloadActions}>
    <div className={styles.downloadButtons}>
      <button type="button" className={styles.secondaryButton} disabled={disabled || Boolean(preparing) || !summary.months.length}
        onClick={() => void download('all')}><AppIcon className={preparing === 'all' ? 'ri-loader-4-line animate-spin' : 'ri-download-line'} />
        {preparing === 'all' ? 'Preparing all months…' : 'Download all months'}</button>
      <button type="button" className={styles.primaryButton} disabled={disabled || Boolean(preparing)}
        onClick={() => void download('month')}><AppIcon className={preparing === 'month' ? 'ri-loader-4-line animate-spin' : 'ri-download-line'} />
        {preparing === 'month' ? 'Preparing PDF…' : 'Download PDF'}</button>
    </div>
    {progress && <p className={styles.downloadStatus} role="status">{progress}</p>}
    {error && <p className={styles.downloadError} role="alert">{error}</p>}
  </div>;
}
