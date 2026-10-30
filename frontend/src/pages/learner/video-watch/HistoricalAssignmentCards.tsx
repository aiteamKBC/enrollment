import type { HistoricalAssignmentContent } from '@/api/reflectionSubmission';
import type { EvidenceRecord } from '@/api/evidence';

/** The same eight wizard sections, populated by attributed original passages. */
export function HistoricalAssignmentCards({ content, step, files, onPreviewFile }: {
  content: HistoricalAssignmentContent;
  step?: number;
  files: EvidenceRecord[];
  onPreviewFile: (file: EvidenceRecord) => void;
}) {
  return <div className="space-y-5">
    {content.notices.length > 0 && <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      {content.notices.map((notice, index) => <p key={index} className="mt-1">{notice}</p>)}
    </div>}
    {content.cards.map((card, index) => (step === undefined || index === step) && <section key={index} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center gap-3 border-b border-purple-100 bg-purple-50/60 px-5 py-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-purple-700 font-bold text-white">{index + 1}</span>
        <h3 className="font-semibold text-slate-900">{card.title}</h3>
      </header>
      <div className="space-y-4 p-5">
        {card.sections.length === 0 && <p className="rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-500">{card.emptyMessage}</p>}
        {card.sections.map((section, sectionIndex) => <article key={sectionIndex} className={`rounded-xl border p-4 ${section.kind === 'feedback' ? 'border-sky-100 bg-sky-50/40' : 'border-slate-100 bg-slate-50/40'}`}>
          <p className="text-xs font-semibold uppercase tracking-wide text-purple-800">{section.label}</p>
          <p className="mt-1 break-words text-xs text-slate-500">{section.source}</p>
          {section.text.length > 3500 ? <details open={step !== undefined} className="mt-3"><summary className="cursor-pointer text-sm font-medium text-slate-700">Read original content</summary><p className="mt-3 max-h-[32rem] overflow-y-auto whitespace-pre-wrap break-words pr-2 text-sm leading-7 text-slate-800">{section.text}</p></details>
            : <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-slate-800">{section.text}</p>}
        </article>)}
        {index === 1 && files.map(file => <button key={file.id} type="button" className="block w-full rounded-xl border border-purple-200 bg-white p-3 text-left text-sm font-medium text-purple-800 hover:bg-purple-50" onClick={() => onPreviewFile(file)}>
          <i className="ri-attachment-2 mr-2" aria-hidden="true" />{file.filename}<span className="ml-2 text-xs">Open original</span>
        </button>)}
      </div>
    </section>)}
  </div>;
}
