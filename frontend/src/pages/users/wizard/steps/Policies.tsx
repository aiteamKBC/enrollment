import { useWizard } from "../WizardContext.helpers";
import { POLICY_DOCS_KBC } from '@/mocks/enrolment-console';
import { StepHeading } from './fields';
import { useText } from '../layout/textsContext';
import { useShowStepErrors } from "../stepErrors.helpers";
import { useLearnerEnrolmentVisuals } from '@/features/learner-enrolment/enrolmentPresentation';
import { Check, FileText } from 'lucide-react';
import styles from './policies.module.css';

export default function Policies() {
  const { draft, setSection } = useWizard();
  const t = useText();
  const learner = useLearnerEnrolmentVisuals();
  const acknowledged = draft.policies.acknowledged;
  // After a refused Next, every document still to acknowledge is marked.
  const showErrors = useShowStepErrors();

  const toggle = (id: string) =>
    setSection('policies', { acknowledged: { ...acknowledged, [id]: !acknowledged[id] } });

  const ackCount = POLICY_DOCS_KBC.filter((d) => acknowledged[d.id]).length;

  return (
    <div className={learner ? styles.policies : undefined}>
      {learner ? <header className={styles.sectionHeader}>
        <span className={styles.headingIcon} aria-hidden="true"><FileText size={27} strokeWidth={1.8} /></span>
        <h2>{t('block.policies.title')}</h2>
        <span className={styles.artwork} aria-hidden="true"><span className={styles.paper}><i /><i /><i /></span><span className={styles.paperCheck}><Check size={20} strokeWidth={3} /></span></span>
      </header> : <StepHeading title={t('block.policies.title')} />}

      {/* Group A — KBC (with acknowledgement) */}
      <div className={learner ? styles.group : 'mb-6'}>
        <div className={learner ? styles.groupHeading : 'flex items-center justify-between mb-2'}>
          <h3 className={learner ? styles.groupTitle : 'text-[13px] font-heading font-semibold text-foreground-800'}>{t('block.policies.group')}</h3>
          <span className={learner ? styles.counter : 'text-[11px] text-foreground-400'}>{ackCount} of {POLICY_DOCS_KBC.length} acknowledged</span>
        </div>
        <div className={learner ? styles.list : 'divide-y divide-foreground-100 border border-foreground-100 rounded-lg'}>
          {POLICY_DOCS_KBC.map((doc) => {
            const outstanding = showErrors && !acknowledged[doc.id];
            return (
            <div key={doc.id} data-outstanding={learner && outstanding ? 'true' : undefined} className={learner ? styles.row : `flex items-start justify-between gap-3 px-3 py-2.5${outstanding ? ' bg-red-50 ring-1 ring-inset ring-red-500' : ''}`}>
              <a href={doc.url} target="_blank" rel="noopener noreferrer" className={learner ? styles.documentLink : 'text-[12px] text-primary-600 hover:underline inline-flex items-center gap-1.5 min-w-0'}>
                {learner ? <span className={styles.pdfIcon} aria-hidden="true"><AppIcon className="ri-file-pdf-2-line" /></span> : <AppIcon className="ri-file-pdf-2-line text-red-500 shrink-0" />}
                <span className={learner ? styles.filename : 'break-all'}>{doc.label}</span>
              </a>
              <label className={learner ? styles.acknowledgement : 'flex items-center gap-1.5 text-[11px] text-foreground-600 cursor-pointer shrink-0'}>
                <input type="checkbox" checked={!!acknowledged[doc.id]} onChange={() => toggle(doc.id)} aria-invalid={outstanding || undefined} className={learner ? styles.checkbox : 'accent-primary-500'} />
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
