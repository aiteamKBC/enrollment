import { BookOpen, CalendarDays } from 'lucide-react';
import { enrolmentAsset } from '@/features/learner-enrolment/enrolmentPresentation';
import styles from './finalSteps.module.css';

/** Decorative heading shared only by these two learner steps. */
export function FinalStepHeader({ title, calendar = false }: { title: string; calendar?: boolean }) {
  const Icon = calendar ? CalendarDays : BookOpen;
  return <header className={`${styles.sectionHeader} ${calendar ? styles.calendarHeader : ''}`}>
    <span className={styles.headingIcon} aria-hidden="true"><Icon size={27} strokeWidth={1.8} /></span>
    <h2>{title}</h2>
    {calendar && <img className={styles.calendarArtwork} src={enrolmentAsset('coach-meetings-calendar.webp')} alt="" aria-hidden="true" draggable={false} />}
  </header>;
}
