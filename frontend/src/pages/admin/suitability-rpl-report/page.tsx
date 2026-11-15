import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FileCheck2, RefreshCw } from 'lucide-react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { useAuth } from '@/hooks/useAuth.helpers';
import { roleNavMap } from '@/mocks/navigation';
import {
  createBlankSuitabilityRplReportPdf,
  suitabilityRplReportFilename,
} from '@/lib/suitabilityRplReportPdf';

const primaryButton = 'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-primary-700 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-55';
const secondaryButton = 'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-primary-200 bg-background-50 px-4 py-2 text-sm font-semibold text-primary-800 transition-colors hover:bg-primary-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-55';

export default function SuitabilityRplReportPage() {
  const { auth } = useAuth();
  const enrolmentNav = roleNavMap.compliance;
  const previewUrlRef = useRef('');
  const [previewUrl, setPreviewUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');

  const refreshPreview = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError('');
    try {
      const document = await createBlankSuitabilityRplReportPdf(signal);
      if (signal?.aborted) return;
      const nextUrl = URL.createObjectURL(document.output('blob'));
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = nextUrl;
      setPreviewUrl(nextUrl);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === 'AbortError')) {
        setError(cause instanceof Error ? cause.message : 'Could not prepare the blank report preview.');
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (auth.account?.role !== 'admin') return undefined;
    const controller = new AbortController();
    void refreshPreview(controller.signal);
    return () => controller.abort();
  }, [auth.account?.role, refreshPreview]);

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);

  async function download() {
    if (downloading) return;
    setDownloading(true);
    setError('');
    try {
      const document = await createBlankSuitabilityRplReportPdf();
      document.save(suitabilityRplReportFilename);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not prepare the blank report download.');
    } finally {
      setDownloading(false);
    }
  }

  if (auth.account?.role !== 'admin') {
    return <div role="alert" className="p-6">Administrator access is required to view report templates.</div>;
  }

  return (
    <WorkspaceShell
      role="compliance"
      roleLabel={enrolmentNav.label}
      navItems={enrolmentNav.items}
      workspaceLabel={enrolmentNav.workspaceLabel}
      pageTitle="Report templates"
      pageSubtitle="Preview and download approved blank enrolment reports"
    >
      <main className="min-w-0 space-y-5 p-3 md:p-6" aria-labelledby="suitability-rpl-heading">
        <section className="rounded-xl border border-primary-100 bg-background-50 p-5 md:p-6">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <div className="mb-3 flex items-center gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-100 text-primary-800" aria-hidden="true">
                  <FileCheck2 size={21} />
                </span>
                <div>
                  <h2 id="suitability-rpl-heading" className="text-xl font-semibold text-foreground-950">Suitability &amp; Recognition of Prior Learning Report</h2>
                  <p className="mt-1 text-sm text-foreground-500">Four-page A4 blank template</p>
                </div>
              </div>
              <p className="text-sm leading-6 text-foreground-700">
                This admin-only template contains all eight approved assessment sections. It includes no learner data, decisions or signatures and is ready for printing or electronic completion in a future phase.
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row lg:shrink-0">
              <button type="button" className={secondaryButton} disabled={loading} onClick={() => { void refreshPreview(); }}>
                <RefreshCw size={17} aria-hidden="true" className={loading ? 'animate-spin' : ''} />
                {loading ? 'Preparing preview...' : 'Refresh preview'}
              </button>
              <button type="button" className={primaryButton} disabled={downloading} onClick={() => { void download(); }}>
                <Download size={17} aria-hidden="true" />
                {downloading ? 'Preparing PDF...' : 'Download blank PDF'}
              </button>
            </div>
          </div>
        </section>

        {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}

        <section aria-label="Blank report preview" className="overflow-hidden rounded-xl border border-primary-100 bg-background-50">
          <div className="flex items-center justify-between gap-3 border-b border-primary-100 px-4 py-3">
            <div>
              <h3 className="text-base font-semibold text-foreground-900">Blank report preview</h3>
              <p className="text-xs text-foreground-500">A4 portrait · 4 pages · generated in your browser</p>
            </div>
            <span className="text-xs font-medium text-primary-800">No learner information</span>
          </div>
          {loading && !previewUrl && <div role="status" className="flex min-h-[32rem] items-center justify-center p-8 text-sm text-foreground-500">Preparing the PDF preview...</div>}
          {!loading && !previewUrl && !error && <div className="flex min-h-[32rem] items-center justify-center p-8 text-sm text-foreground-500">Preview unavailable. Refresh to try again.</div>}
          {previewUrl && (
            <iframe
              title="Suitability and Recognition of Prior Learning blank report preview"
              src={`${previewUrl}#view=FitH`}
              className="h-[72vh] min-h-[38rem] w-full bg-white"
            />
          )}
        </section>
      </main>
    </WorkspaceShell>
  );
}
