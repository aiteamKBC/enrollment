import type { ReactNode } from 'react';
import { ClipboardCheck, ClipboardList, Coins, FileText, GraduationCap, HeartHandshake, Layers3, LayoutGrid, ChartNoAxesCombined, BriefcaseBusiness } from 'lucide-react';
import { RichTextBlocks } from '@/pages/users/wizard/layout/RichText';
import { useLearnerEnrolmentVisuals } from './enrolmentPresentation';
import { informationSections, LearnerStepPresentationContext } from './stepPresentation';
import styles from './learnerSteps.module.css';

const STEPS = new Set(['before-you-begin', 'ilr-details', 'ilr', 'programme-suitability', 'plr', 'inclusion', 'cv-job', 'skills-radar']);

export function LearnerStepContent({ slug, children }: { slug?: string; children: ReactNode }) {
  const learner = useLearnerEnrolmentVisuals();
  if (!learner || !slug || !STEPS.has(slug)) return <>{children}</>;
  return <LearnerStepPresentationContext.Provider value={slug}>
    <div className={styles.content} data-enrolment-step={slug}>{children}</div>
  </LearnerStepPresentationContext.Provider>;
}

export function StepArtwork({ slug }: { slug: string }) {
  const Icon = slug === 'programme-suitability' ? LayoutGrid : slug === 'cv-job' ? BriefcaseBusiness
    : slug === 'skills-radar' ? ChartNoAxesCombined : slug === 'inclusion' ? HeartHandshake
      : slug === 'plr' ? GraduationCap : ClipboardList;
  return <span className={styles.artwork} aria-hidden="true"><Icon strokeWidth={1.4} /></span>;
}

export function BeforeYouBeginContent({ title, source }: { title: string; source: string }) {
  const [lead = [], ...sections] = informationSections(source);
  const icons = [Coins, FileText, ClipboardCheck, Layers3];
  const classes = { container: styles.rich, heading: styles.informationHeading, paragraph: styles.paragraph,
    list: styles.list, subheading: styles.informationHeading };
  return <article className={styles.before}>
    <div className={styles.stepIntro}><StepArtwork slug="before-you-begin" /><div>
      <h2>{title}</h2><RichTextBlocks blocks={lead} classes={classes} />
    </div></div>
    <div className={styles.informationGrid}>{sections.map((blocks, index) => {
      const Icon = icons[index % icons.length];
      return <section className={styles.informationCard} key={index}>
        <span className={styles.sectionIcon} aria-hidden="true"><Icon size={26} /></span>
        <RichTextBlocks blocks={blocks} classes={classes} />
      </section>;
    })}</div>
  </article>;
}
