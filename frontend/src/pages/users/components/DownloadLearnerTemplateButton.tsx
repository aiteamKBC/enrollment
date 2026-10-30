import { useRef, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { fetchEnrolmentUserTemplate } from '@/api/enrolmentUserImport';
import { btnSecondary } from './ui';

export function DownloadLearnerTemplateButton() {
  const busy = useRef(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');

  const download = async () => {
    if (busy.current) return;
    busy.current = true;
    setDownloading(true);
    setError('');
    try {
      const blob = await fetchEnrolmentUserTemplate();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'learners-import-template.xlsx';
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Give the browser time to start consuming the download before cleanup.
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not download the template.');
    } finally {
      busy.current = false;
      setDownloading(false);
    }
  };

  return (
    <div>
      <button type="button" className={`${btnSecondary} disabled:cursor-not-allowed disabled:opacity-50`} onClick={download} disabled={downloading}>
        <AppIcon className={downloading ? 'ri-loader-4-line animate-spin' : 'ri-file-excel-2-line'} />
        {downloading ? 'Downloading...' : 'Download template'}
      </button>
      {error && <p role="alert" className="mt-1 max-w-xs text-[12px] text-red-600">{error}</p>}
    </div>
  );
}
