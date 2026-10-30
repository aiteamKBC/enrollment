import { useState } from 'react';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { roleNavMap } from '@/mocks/navigation';

const employerNav = roleNavMap.employer;

interface DocumentToSign {
  id: string;
  apprentice: string;
  initials: string;
  programme: string;
  title: string;
  type: string;
  issuedDate: string;
  dueDate: string;
  status: 'Awaiting Signature' | 'Signed' | 'Overdue';
  description: string;
  pages: number;
}

const DOCUMENTS: DocumentToSign[] = [
  { id: 'ds-01', apprentice: 'Sophie Williams', initials: 'SW', programme: 'Marketing Executive L4', title: 'Commitment Statement', type: 'Statutory', issuedDate: '15 May 2026', dueDate: '30 May 2026', status: 'Signed', description: 'Apprenticeship commitment statement outlining roles, responsibilities, and training schedule', pages: 4 },
  { id: 'ds-02', apprentice: 'Sophie Williams', initials: 'SW', programme: 'Marketing Executive L4', title: 'Employer Agreement — Training Costs', type: 'Financial', issuedDate: '10 May 2026', dueDate: '25 May 2026', status: 'Signed', description: 'Agreement confirming employer contribution to training costs and commitment to off-the-job training', pages: 3 },
  { id: 'ds-03', apprentice: 'Sophie Williams', initials: 'SW', programme: 'Marketing Executive L4', title: 'Progress Review — May 2026', type: 'Review', issuedDate: '28 May 2026', dueDate: '11 Jun 2026', status: 'Awaiting Signature', description: 'Monthly progress review for May 2026. Sophie is progressing well. Requires employer signature.', pages: 2 },
  { id: 'ds-04', apprentice: 'Tom Richards', initials: 'TR', programme: 'Marketing Executive L4', title: 'Progress Review — May 2026', type: 'Review', issuedDate: '28 May 2026', dueDate: '11 Jun 2026', status: 'Awaiting Signature', description: 'Monthly progress review for May 2026. Tom needs attendance improvement plan acknowledgement.', pages: 2 },
  { id: 'ds-05', apprentice: 'Tom Richards', initials: 'TR', programme: 'Marketing Executive L4', title: 'Attendance Improvement Plan', type: 'Action Plan', issuedDate: '3 Jun 2026', dueDate: '10 Jun 2026', status: 'Overdue', description: 'Plan outlining steps to improve attendance from 82% to 90% target. Requires employer acknowledgement.', pages: 3 },
  { id: 'ds-06', apprentice: 'Daniel Clarke', initials: 'DC', programme: 'Business Admin L3', title: 'Training Plan Amendment', type: 'Plan', issuedDate: '1 Jun 2026', dueDate: '15 Jun 2026', status: 'Awaiting Signature', description: 'Updated training plan incorporating additional business communication modules at employer request', pages: 5 },
  { id: 'ds-07', apprentice: 'Daniel Clarke', initials: 'DC', programme: 'Business Admin L3', title: 'Quarterly Review — Q1 2026', type: 'Review', issuedDate: '28 Mar 2026', dueDate: '12 Apr 2026', status: 'Signed', description: 'Q1 2026 quarterly review. Daniel exceeding all targets.', pages: 3 },
  { id: 'ds-08', apprentice: 'Mark Jensen', initials: 'MJ', programme: 'Digital Marketer L3', title: 'KSB Portfolio Sign-off', type: 'Assessment', issuedDate: '5 Jun 2026', dueDate: '22 Jun 2026', status: 'Awaiting Signature', description: 'Final KSB portfolio for Mark Jensen. Employer confirmation of workplace evidence authenticity.', pages: 8 },
  { id: 'ds-09', apprentice: 'Mark Jensen', initials: 'MJ', programme: 'Digital Marketer L3', title: 'Gateway Readiness Declaration', type: 'Gateway', issuedDate: '1 Jun 2026', dueDate: '20 Jun 2026', status: 'Awaiting Signature', description: 'Declaration that Mark is ready for end-point assessment gateway', pages: 2 },
  { id: 'ds-10', apprentice: 'Rachel Thompson', initials: 'RT', programme: 'Data Analyst L4', title: 'Progress Review — May 2026', type: 'Review', issuedDate: '20 May 2026', dueDate: '3 Jun 2026', status: 'Signed', description: 'Monthly progress review. Strong data analysis skills demonstrated.', pages: 2 },
];

export default function EmployerDocumentsToSign() {
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [signingDoc, setSigningDoc] = useState<Record<string, boolean>>({});

  const filtered = DOCUMENTS.filter(d => {
    if (search && !d.title.toLowerCase().includes(search.toLowerCase()) && !d.apprentice.toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter !== 'all' && d.status !== statusFilter) return false;
    return true;
  });

  const awaiting = DOCUMENTS.filter(d => d.status === 'Awaiting Signature').length;
  const overdue = DOCUMENTS.filter(d => d.status === 'Overdue').length;
  const signed = DOCUMENTS.filter(d => d.status === 'Signed').length;

  const handleSign = (id: string) => {
    setSigningDoc(prev => ({ ...prev, [id]: true }));
    setTimeout(() => setSigningDoc(prev => ({ ...prev, [id]: false })), 2000);
  };

  return (
    <WorkspaceShell role="employer" roleLabel={employerNav.label} navItems={employerNav.items} workspaceLabel={employerNav.workspaceLabel} pageTitle="Documents to Sign" pageSubtitle="Review and sign apprenticeship documents requiring employer authorisation" userName="Lauren Mitchell" userRole="Line Manager — Tim Hortons UK">
      <div className="p-6 space-y-6">
        {/* Hero Banner */}
        <div className="relative rounded-2xl overflow-hidden" style={{ background: 'linear-gradient(180deg, oklch(var(--primary-950)) 0%, oklch(var(--primary-900)) 50%, oklch(var(--primary-800)) 100%)' }}>
          <div className="absolute inset-x-0 top-0 h-px bg-white/10" />
          <div className="absolute inset-x-0 bottom-0 h-px bg-white/5" />
          <div className="relative p-6 sm:p-8 flex flex-col sm:flex-row items-start sm:items-center gap-5">
            <span className="w-14 h-14 rounded-2xl bg-white/20 backdrop-blur-sm flex items-center justify-center shrink-0">
              <AppIcon className="ri-pen-nib-line text-white text-2xl"></AppIcon>
            </span>
            <div className="flex-1">
              <h2 className="text-lg font-heading font-bold text-white mb-1">Documents to Sign</h2>
              <p className="text-[13px] text-white/80 leading-relaxed">
                <strong>{DOCUMENTS.length} documents</strong> · {awaiting} awaiting signature · {overdue} overdue · {signed} signed
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <div className="bg-white/15 backdrop-blur-sm rounded-xl px-4 py-3 text-center">
                <p className="text-2xl font-bold text-red-300">{awaiting + overdue}</p>
                <p className="text-[10px] text-white/70 uppercase tracking-wide">Action Needed</p>
              </div>
              <div className="bg-white/15 backdrop-blur-sm rounded-xl px-4 py-3 text-center">
                <p className="text-2xl font-bold text-emerald-300">{signed}</p>
                <p className="text-[10px] text-white/70 uppercase tracking-wide">Signed</p>
              </div>
            </div>
          </div>
        </div>

        {/* Overdue Alert */}
        {overdue > 0 && (
          <div className="bg-red-50 border border-red-200/50 rounded-xl p-4 flex flex-col sm:flex-row items-start sm:items-center gap-3">
            <span className="w-9 h-9 rounded-lg bg-red-100 flex items-center justify-center shrink-0">
              <AppIcon className="ri-error-warning-line text-red-600 text-base"></AppIcon>
            </span>
            <div className="flex-1">
              <p className="text-sm font-semibold text-red-800">{overdue} document{overdue > 1 ? 's' : ''} overdue!</p>
              <p className="text-[12px] text-red-600 mt-0.5">Overdue documents may affect funding compliance. Please sign immediately.</p>
            </div>
            <button className="px-4 py-2 bg-red-600 text-white rounded-lg text-[12px] font-semibold hover:bg-red-700 transition-smooth cursor-pointer whitespace-nowrap">
              <AppIcon className="ri-pen-nib-line mr-1"></AppIcon> Sign Overdue
            </button>
          </div>
        )}

        {/* Filters */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <div className="relative flex-1 sm:max-w-sm">
            <AppIcon className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-foreground-400 text-sm"></AppIcon>
            <input type="text" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search documents..." className="w-full pl-9 pr-3 py-2 bg-background-50 border border-foreground-200/60 rounded-lg text-[13px] text-foreground-900 placeholder:text-foreground-400 focus:outline-none focus:border-primary-300" />
          </div>
          <div className="flex items-center gap-1 bg-background-100 rounded-xl p-1">
            {[{ key: 'all', label: 'All', count: DOCUMENTS.length },{ key: 'Awaiting Signature', label: 'Awaiting', count: awaiting },{ key: 'Overdue', label: 'Overdue', count: overdue },{ key: 'Signed', label: 'Signed', count: signed }].map(f => (
              <button key={f.key} onClick={() => setStatusFilter(f.key)} className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-smooth whitespace-nowrap cursor-pointer ${statusFilter === f.key ? 'bg-background-50 text-foreground-900 shadow-sm' : 'text-foreground-500 hover:text-foreground-700'}`}>
                {f.label} <span className="ml-1 text-[10px] opacity-60">{f.count}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Documents Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filtered.map(doc => {
            const typeIcon: Record<string, string> = {
              'Statutory': 'ri-government-line',
              'Financial': 'ri-money-pound-circle-line',
              'Review': 'ri-file-chart-line',
              'Action Plan': 'ri-clipboard-line',
              'Plan': 'ri-road-map-line',
              'Assessment': 'ri-bar-chart-2-line',
              'Gateway': 'ri-flag-line',
            };

            return (
              <div key={doc.id} className={`bg-background-50 rounded-xl border p-5 card-premium ${doc.status === 'Overdue' ? 'border-red-200/50 bg-red-50/10' : doc.status === 'Awaiting Signature' ? 'border-amber-200/50' : 'border-foreground-200/60'}`}>
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${doc.status === 'Overdue' ? 'bg-red-100 text-red-600' : doc.status === 'Awaiting Signature' ? 'bg-amber-100 text-amber-600' : 'bg-emerald-100 text-emerald-600'}`}>
                      <AppIcon className={`${typeIcon[doc.type] || 'ri-file-text-line'} text-sm`}></AppIcon>
                    </div>
                    <div>
                      <p className="text-[13px] font-semibold text-foreground-900">{doc.title}</p>
                      <p className="text-[11px] text-foreground-400">{doc.type} · {doc.pages} pages</p>
                    </div>
                  </div>
                  <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${
                    doc.status === 'Signed' ? 'bg-emerald-100 text-emerald-700' :
                    doc.status === 'Overdue' ? 'bg-red-100 text-red-700' :
                    'bg-amber-100 text-amber-700'
                  }`}>{doc.status}</span>
                </div>

                <div className="flex items-center gap-3 mb-3">
                  <div className="w-8 h-8 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center ring-1 ring-primary-200">
                    <span className="text-[10px] font-bold">{doc.initials}</span>
                  </div>
                  <div>
                    <p className="text-[12px] font-medium text-foreground-800">{doc.apprentice}</p>
                    <p className="text-[10px] text-foreground-400">{doc.programme}</p>
                  </div>
                </div>

                <p className="text-[11px] text-foreground-500 mb-3">{doc.description}</p>

                <div className="flex items-center gap-4 text-[11px] text-foreground-400 mb-3">
                  <span><AppIcon className="ri-calendar-line mr-1"></AppIcon> Issued: {doc.issuedDate}</span>
                  <span className={doc.status === 'Overdue' ? 'text-red-500 font-semibold' : ''}>
                    <AppIcon className="ri-timer-line mr-1"></AppIcon> Due: {doc.dueDate}
                  </span>
                </div>

                <div className="flex items-center gap-2 pt-3 border-t border-background-100">
                  {doc.status !== 'Signed' ? (
                    <>
                      <button onClick={() => handleSign(doc.id)} disabled={signingDoc[doc.id]} className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-lg text-[12px] font-semibold hover:bg-primary-600 transition-smooth cursor-pointer whitespace-nowrap disabled:opacity-50 text-center">
                        {signingDoc[doc.id] ? <><AppIcon className="ri-check-line mr-1"></AppIcon> Signed!</> : <><AppIcon className="ri-pen-nib-line mr-1"></AppIcon> Sign Now</>}
                      </button>
                      <button className="px-3 py-2 bg-background-50 border border-background-200 rounded-lg text-[12px] font-medium text-foreground-600 hover:bg-background-100 transition-smooth cursor-pointer whitespace-nowrap">
                        <AppIcon className="ri-file-search-line mr-1"></AppIcon> Preview
                      </button>
                    </>
                  ) : (
                    <div className="flex items-center gap-2 w-full">
                      <button className="flex-1 px-4 py-2 bg-background-100 text-emerald-700 rounded-lg text-[12px] font-semibold cursor-pointer whitespace-nowrap text-center">
                        <AppIcon className="ri-check-double-line mr-1"></AppIcon> Signed
                      </button>
                      <button className="px-3 py-2 bg-background-50 border border-background-200 rounded-lg text-[12px] font-medium text-foreground-600 hover:bg-background-100 transition-smooth cursor-pointer whitespace-nowrap">
                        <AppIcon className="ri-download-line mr-1"></AppIcon> Download
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </WorkspaceShell>
  );
}