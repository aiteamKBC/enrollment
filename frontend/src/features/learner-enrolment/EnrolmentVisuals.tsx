import { BriefcaseBusiness, ChartColumnIncreasing, CirclePlay, ClipboardList, Files, FileText, GraduationCap, HeartHandshake, House, Layers3, LayoutGrid, LockKeyhole, Monitor, ShieldCheck, Sparkles, Star, Target, UserRound } from 'lucide-react';
import { RichTextBlocks } from '@/pages/users/wizard/layout/RichText';
import { enrolmentAsset, welcomeSections } from './enrolmentPresentation';
import styles from './learnerEnrolment.module.css';

export function LearnerNavIcon({ path }: { path: string }) {
  const icons = { '/learner/onboarding': GraduationCap, '/learner/onboarding/reviews': Star, '/learner/compliance-documents': Files, '/learner/first-session': CirclePlay, '/lms': Monitor };
  const Icon = icons[path.split('?')[0] as keyof typeof icons] || GraduationCap;
  return <Icon size={24} strokeWidth={1.8} aria-hidden="true" />;
}

/** Icons identify the configured step slug; labels/order/status stay in the wizard. */
export function EnrolmentStepIcon({ slug, locked = false }: { slug: string; locked?: boolean }) {
  const icons = { introduction: House, 'before-you-begin': ClipboardList, 'personal-details': UserRound, 'ilr-details': UserRound,
    ilr: Layers3, 'programme-suitability': LayoutGrid, 'cv-job': BriefcaseBusiness, plr: FileText,
    'skills-radar': ChartColumnIncreasing, inclusion: HeartHandshake, policies: ShieldCheck, 'initial-assessments': ClipboardList, 'next-steps': GraduationCap };
  const Icon = locked ? LockKeyhole : icons[slug as keyof typeof icons] || Layers3;
  return <Icon size={21} strokeWidth={1.8} aria-hidden="true" />;
}

export function EnrolmentHero() {
  return <section className={styles.hero} aria-labelledby="enrolment-hero-title" data-testid="enrolment-hero">
    <div className={styles.heroSky} aria-hidden="true">
      <svg viewBox="0 60 1500 175" preserveAspectRatio="xMinYMax slice">
        <image href={enrolmentAsset('images/kbc-enrolment-hero.png')} width="2172" height="724" />
      </svg>
    </div>
    <div className={styles.heroMedia}>
      <img className={styles.heroEnvironment} src={enrolmentAsset('images/kbc-enrolment-hero.png')} alt="" aria-hidden="true" />
      <img className={styles.heroImage} src={enrolmentAsset('images/kbc-enrolment-hero.png')} alt="Kent Business College building" />
    </div>
    <div className={styles.heroSurface} aria-hidden="true">
      <svg className={styles.heroWaves} viewBox="0 0 1400 150" preserveAspectRatio="none"><path d="M630 0H1400V150H1110C870 166 881 18 630 0Z" fill="white" opacity=".2"/><path d="M730 0H840C1010 146 1120 187 1400 86V150H1110C956 145 937 40 730 0Z" fill="#e6b5ff" opacity=".5"/></svg>
    </div>
    <div className={styles.heroContent}><span className={styles.heroIcon}><GraduationCap size={38} strokeWidth={1.7} aria-hidden="true" /></span>
      <div><h2 id="enrolment-hero-title">Your enrolment</h2><p>Please complete every step and sign where asked. Your answers save as you go, and the enrolment team will review them once you submit.</p></div>
    </div>
  </section>;
}

export function WelcomeContent({ title, source }: { title: string; source: string }) {
  const { lead, cards, conclusion } = welcomeSections(source);
  const cardIcons = [GraduationCap, ChartColumnIncreasing, Sparkles];
  const classes = { container: styles.richText, heading: styles.welcomeSectionHeading, subheading: styles.welcomeSubheading, paragraph: styles.welcomeParagraph, list: styles.welcomeList };
  return <article className={styles.welcome} data-testid="enrolment-welcome">
    <div className={styles.welcomeLead}><div className={styles.welcomeGlow} aria-hidden="true" /><h2 className={styles.welcomeTitle}>{title}</h2><RichTextBlocks blocks={lead} classes={classes} /></div>
    {cards.length > 0 && <div className={styles.welcomeCards}>{cards.map((blocks, i) => {
      const Icon = cardIcons[i % cardIcons.length];
      return <section key={i} className={styles.welcomeCard}><span className={styles.cardIcon}><Icon size={28} strokeWidth={1.8} aria-hidden="true" /></span><RichTextBlocks blocks={blocks} classes={classes} /></section>;
    })}</div>}
    {conclusion.length > 0 && <div className={styles.welcomeConclusion}><Target size={42} strokeWidth={1.7} aria-hidden="true" /><RichTextBlocks blocks={conclusion} classes={classes} /></div>}
  </article>;
}
