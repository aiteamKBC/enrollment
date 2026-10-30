import { SkeletonBlock } from '@/components/feature/Skeletons';
import design from './design.module.css';
import journal from './journal.module.css';
import report from './report.module.css';
import shell from './shell.module.css';
import { PageContainer } from '@/components/ui/PageContainer';

/** Content placeholders shared by the page and its initial route fallback. */
export function MonthReportSkeleton() {
  return <div role="status" aria-label="Loading monthly report" aria-busy="true">
    <span className="sr-only">Loading monthly report…</span>
    <div className={`${design.reportPage} ${journal.page}`} aria-hidden="true">
      <div className={`${journal.card} ${journal.filters}`}>
        {[0, 1].map(index => <div key={index} className={journal.filterField}>
          <SkeletonBlock className="h-2.5 w-24" /><SkeletonBlock className="h-[34px] w-full rounded-md" />
        </div>)}
      </div>
      <div className={`${journal.card} ${journal.header}`}>
        <div className={journal.headerTop}>
          <SkeletonBlock className="h-7 w-48 max-w-full" />
          <div className={journal.headerActions}>
            <SkeletonBlock className="h-12 w-28 rounded-lg" />
            <SkeletonBlock className="h-[34px] w-36 rounded-md" />
            <SkeletonBlock className="h-[34px] w-28 rounded-md" />
          </div>
        </div>
        <div className={journal.profileGrid}>{[0, 1, 2].map(index => <div key={index} className={journal.profileGroup}>
          {[0, 1].map(field => <div key={field} className="space-y-2">
            <SkeletonBlock className="h-2 w-20 max-w-full" /><SkeletonBlock className="h-3 w-3/4" />
          </div>)}
        </div>)}</div>
      </div>
      <div className={`${journal.card} ${journal.hours}`}>
        <div className={`${journal.hoursHeading} space-y-2`}>
          <SkeletonBlock className="h-2.5 w-36" /><SkeletonBlock className="h-2.5 w-56 max-w-full" />
        </div>
        <div className={journal.hoursGrid}>{[0, 1].map(index => <div key={index} className={`${journal.metric} ${index ? journal.acceptedMetric : ''} space-y-3`}>
          <SkeletonBlock className="h-2 w-28" /><SkeletonBlock className="h-5 w-24" /><SkeletonBlock className="h-2 w-40 max-w-full" />
        </div>)}</div>
        <SkeletonBlock className="mt-3 h-2.5 w-32" />
      </div>
      <div className={`${journal.card} ${journal.activityLog}`}>
        <div className={journal.sectionHeading}><div className="w-full space-y-2">
          <SkeletonBlock className="h-4 w-28" /><SkeletonBlock className="h-3 w-72 max-w-full" />
        </div><SkeletonBlock className="h-6 w-20 shrink-0 rounded-full" /></div>
        <div className={journal.tableCaption}><SkeletonBlock className="mb-2 h-7 w-32" /></div>
        <div className={report.activityTableWrap}><table className={report.activityTable}>
          <thead><tr>{[0, 1, 2, 3, 4, 5].map(column => <th key={column}><SkeletonBlock className="h-2.5 w-16 max-w-full" /></th>)}</tr></thead>
          <tbody>{[0, 1].map(row => <tr key={row} className={report.activityRow}>
            <td><SkeletonBlock className="h-3 w-20 max-w-full" /></td><td><SkeletonBlock className="h-3 w-16 max-w-full" /></td>
            <td className={report.activityCell}><div className="space-y-3 py-2">
              <SkeletonBlock className="h-3 w-4/5" /><SkeletonBlock className="h-2.5 w-2/3" /><SkeletonBlock className="h-2.5 w-1/2" />
            </div></td>
            <td><SkeletonBlock className="h-3 w-24 max-w-full" /></td><td><SkeletonBlock className="h-3 w-16 max-w-full" /></td>
            <td><SkeletonBlock className="h-5 w-20 max-w-full rounded-full" /></td>
          </tr>)}</tbody>
        </table></div>
        <div className={journal.totals}><SkeletonBlock className="h-2.5 w-64 max-w-full" /></div>
      </div>
      <div className={`${journal.card} ${journal.signoff}`}>
        <div className={journal.sectionHeading}><div className="w-full space-y-2">
          <SkeletonBlock className="h-4 w-36" /><SkeletonBlock className="h-3 w-72 max-w-full" />
        </div></div>
        <div className={`${journal.signoffBody} space-y-3`}>
          <SkeletonBlock className="h-8 w-full" /><SkeletonBlock className="h-20 w-full" /><SkeletonBlock className="h-20 w-full" />
        </div>
      </div>
    </div>
  </div>;
}

export function MonthListSkeleton() {
  return <div role="status" aria-label="Loading monthly records" aria-busy="true">
    <span className="sr-only">Loading monthly records…</span>
    <div className={`${design.monthList} ${design.loadingList}`} aria-hidden="true">
      <div className={`${design.monthListHeader} space-y-2`}>
        <SkeletonBlock className="h-2.5 w-36" /><SkeletonBlock className="h-7 w-80 max-w-full" /><SkeletonBlock className="h-3 w-64 max-w-full" />
      </div>
      <div className={`${design.card} space-y-4 p-5`}><SkeletonBlock className="h-6 w-36" /><SkeletonBlock className="h-2 w-full" /><SkeletonBlock className="h-3 w-48 max-w-full" /></div>
      <div className={`${design.card} grid grid-cols-2 gap-4 p-5 md:grid-cols-4`}>{[0, 1, 2, 3].map(index => <div key={index} className="flex min-w-0 items-center gap-3"><SkeletonBlock className="h-9 w-9 shrink-0 rounded-xl" /><div className="min-w-0 flex-1 space-y-2"><SkeletonBlock className="h-4 w-16 max-w-full" /><SkeletonBlock className="h-2.5 w-20 max-w-full" /></div></div>)}</div>
      <div className={`${design.card} grid gap-3 p-4 lg:grid-cols-[minmax(0,1fr)_auto_auto]`}><SkeletonBlock className="h-9 w-full" /><SkeletonBlock className="h-8 w-56 max-w-full" /><SkeletonBlock className="h-8 w-36 max-w-full" /></div>
      <div className={design.bulkActions}><SkeletonBlock className="h-3 w-64 max-w-full" /><SkeletonBlock className="h-9 w-36 rounded-lg" /></div>
      <div className={design.yearGroups}>{[0, 1].map(year => <div key={year} className={design.yearGroup}>
        <div className={design.yearHeading}><SkeletonBlock className="h-6 w-52 max-w-[65%]" /><SkeletonBlock className="h-4 w-24 max-w-[30%]" /></div>
        <div className={design.monthGrid}>{[0, 1, 2].map(index => <div key={index} className={`${design.card} ${design.monthCard}`}>
          <div className={design.monthCardHeading}><SkeletonBlock className="h-6 w-7 shrink-0 rounded-full" /><SkeletonBlock className="h-4 w-36 max-w-[55%]" /><SkeletonBlock className="ml-auto h-2.5 w-14" /></div>
          <div className={design.monthHours}>{[0, 1].map(metric => <div key={metric} className="space-y-2"><SkeletonBlock className="h-2 w-20 max-w-full" /><SkeletonBlock className="h-4 w-16 max-w-full" /></div>)}</div>
          <div className={design.signatureChips}><SkeletonBlock className="h-3 w-28" /><SkeletonBlock className="h-3 w-28" /></div>
          <div className={design.monthCardFooter}><SkeletonBlock className="h-5 w-16 rounded-full" /><SkeletonBlock className="h-4 w-24" /></div>
        </div>)}</div>
      </div>)}</div>
    </div>
  </div>;
}

export function RecordPageSkeleton({ reportPage = false }: { reportPage?: boolean }) {
  return <div className={`${design.scope} ${shell.shell}`}>
    <header className={shell.header} aria-hidden="true">
      <div className={shell.brand}>
        <img src="/assets/kbc-logo.png" alt="" className={shell.logo} />
        <div className={shell.brandText}><p>Training &amp; Development Reports</p><span>Monthly Activity Logs</span></div>
      </div>
      <div className={shell.actions}><span className={shell.college}>Kent Business College</span><SkeletonBlock className="h-9 w-20 rounded-md" /></div>
    </header>
    <main className={shell.main}>
      <PageContainer className={`${design.page} ${shell.content} ${reportPage ? journal.canvas : shell.monthsCanvas}`}>
        <SkeletonBlock className="mb-1 h-[34px] w-16 rounded-md" />
        {reportPage ? <MonthReportSkeleton /> : <MonthListSkeleton />}
      </PageContainer>
    </main>
  </div>;
}
