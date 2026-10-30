import { useWizard } from "../WizardContext.helpers";
import { POLICY_DOCS_KBC } from '@/mocks/enrolment-console';
import { StepHeading } from './fields';
import { useText } from '../layout/textsContext';
import { useShowStepErrors } from "../stepErrors.helpers";

export default function Policies() {
  const { draft, setSection } = useWizard();
  const t = useText();
  const acknowledged = draft.policies.acknowledged;
  // After a refused Next, every document still to acknowledge is marked.
  const showErrors = useShowStepErrors();

  const toggle = (id: string) =>
    setSection('policies', { acknowledged: { ...acknowledged, [id]: !acknowledged[id] } });

  const ackCount = POLICY_DOCS_KBC.filter((d) => acknowledged[d.id]).length;

  return (
    <div>
      <StepHeading title={t('block.policies.title')} />

      {/* Group A — KBC (with acknowledgement) */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[13px] font-heading font-semibold text-foreground-800">{t('block.policies.group')}</h3>
          <span className="text-[11px] text-foreground-400">{ackCount} of {POLICY_DOCS_KBC.length} acknowledged</span>
        </div>
        <div className="divide-y divide-foreground-100 border border-foreground-100 rounded-lg">
          {POLICY_DOCS_KBC.map((doc) => {
            const outstanding = showErrors && !acknowledged[doc.id];
            return (
            <div key={doc.id} className={`flex items-start justify-between gap-3 px-3 py-2.5${outstanding ? ' bg-red-50 ring-1 ring-inset ring-red-500' : ''}`}>
              <a href={doc.url} target="_blank" rel="noopener noreferrer" className="text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0">
                <AppIcon className="ri-file-pdf-2-line text-red-500 shrink-0" />
                <span className="break-all">{doc.label}</span>
              </a>
              <label className="flex items-center gap-1.5 text-[11px] text-foreground-600 cursor-pointer shrink-0">
                <input type="checkbox" checked={!!acknowledged[doc.id]} onChange={() => toggle(doc.id)} aria-invalid={outstanding || undefined} className="accent-primary-500" />
                <span className={outstanding ? 'text-red-600' : undefined}>{t('block.policies.acknowledge')}</span>
              </label>
            </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
