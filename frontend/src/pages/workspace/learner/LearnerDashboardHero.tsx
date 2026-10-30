import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, ArrowRight, BookOpen, BriefcaseBusiness, Building2, CalendarDays, ChevronRight, Clock, GraduationCap, Mail, Map, Phone, Play, UserRound, UsersRound } from 'lucide-react';
import { statusTone, toneStyle } from '@/lib/statusTone';
import { SeasonalHoverCards, type SeasonCardProps } from '@/components/lightswind/seasonal-hover-cards';
import styles from './LearnerDashboardHero.module.css';

interface LearnerDashboardHeroProps {
  avatar: ReactNode;
  name: string;
  description?: string;
  cohort: string;
  moduleLabel: string;
  modules: { id: string; title: string; href: string }[];
  modulePlaceholder: string;
  allModulesHref: string;
  actionCards?: SeasonCardProps[];
  employer: string;
  organization: string;
  coach: string;
  coachEmail?: string;
  coachPhone?: string;
  status: string;
  startDate: string;
  plannedEnd: string;
  loading: boolean;
  onContinue: () => void;
  onOpenMap: () => void;
  observerAction?: { label: string; onClick: () => void };
  readOnlyModules?: boolean;
  handwritingText?: { firstLine: string; secondLine: string };
}

/** Presentation only: placement, dates, status and navigation come from the dashboard. */
export function LearnerDashboardHero(props: LearnerDashboardHeroProps) {
  const { avatar, name, description, cohort, moduleLabel, modules, modulePlaceholder,
    allModulesHref, actionCards = [], employer, organization, coach, coachEmail, coachPhone, status, startDate, plannedEnd, loading,
    onContinue, onOpenMap, observerAction, readOnlyModules = false,
    handwritingText = { firstLine: 'Keep', secondLine: 'learning' } } = props;
  const tone = statusTone(status);
  return (
    <header className={`${styles.hero} ${observerAction ? styles.observerHero : ''}`} aria-label="Learner programme">
      <div className={styles.artwork} aria-hidden="true" />
      <div className={styles.top}>
        <div className={styles.identity}>
          <div className={styles.avatar}>
            <span role="img" className={styles.statusBadge} data-tone={tone} aria-label={`Status: ${status}`} title={`Status: ${status}`}>
              <span className={toneStyle(tone).dot} />
            </span>
            {avatar}
          </div>
          <div className={styles.identityText}>
            <p className={styles.eyebrow}>Learner</p>
            <h1 className={styles.name}>{name}</h1>
            {description && <p className={styles.description}>{description}</p>}
            <div className={styles.headerFacts} aria-label="Programme details">
              <span className={styles.headerFact}>
                <UsersRound aria-hidden="true" />
                <span><span className={styles.headerFactLabel}>Cohort</span><strong>{cohort}</strong></span>
              </span>
              <span className={styles.headerFact}>
                <Clock aria-hidden="true" />
                <span><span className={styles.headerFactLabel}>Start date</span><strong>{startDate}</strong></span>
              </span>
              <span className={styles.headerFact}>
                <CalendarDays aria-hidden="true" />
                <span><span className={styles.headerFactLabel}>End date</span><strong>{plannedEnd}</strong></span>
              </span>
            </div>
            <p className={styles.quote}>“Progress turns goals into reality.”</p>
          </div>
        </div>
        <div className={styles.handwriting} aria-hidden="true">
          {handwritingText.firstLine}<br /><span>{handwritingText.secondLine}</span>
        </div>
        <div className={styles.actionArea}>
          <div className={styles.actions}>
            {observerAction ? <button type="button" onClick={observerAction.onClick} className={styles.action}>
              <ArrowLeft aria-hidden="true" />{observerAction.label}
            </button> : <>
              <button type="button" onClick={onContinue} disabled={loading} aria-busy={loading}
                className={`${styles.action} ${styles.primaryAction}`}>
                <Play aria-hidden="true" />Continue learning<ArrowRight aria-hidden="true" className={styles.actionArrow} />
              </button>
              <button type="button" onClick={onOpenMap} className={styles.action}>
                <Map aria-hidden="true" />Learner&apos;s Map
              </button>
            </>}
          </div>
          {!observerAction && <p className={styles.actionCaption}>Your learning journey. Further possibilities.</p>}
        </div>
      </div>

      <div className={`${styles.cards} ${actionCards.length > 0 ? styles.cardsWithActions : ''}`}>
        <section className={`${styles.card} ${styles.contactCard}`} aria-label="Employer and Organization">
          <div className={styles.factRow}>
            <span className={styles.icon}><BriefcaseBusiness aria-hidden="true" /></span>
            <dl><dt>Employer</dt><dd>{employer}</dd></dl>
          </div>
          <div className={styles.factRow}>
            <span className={styles.icon}><Building2 aria-hidden="true" /></span>
            <dl><dt>Organization</dt><dd>{organization}</dd></dl>
          </div>
        </section>
        <section className={`${styles.card} ${styles.coachCard}`} aria-label="Coach">
          <div className={styles.factRow}>
            <span className={styles.icon}><UserRound aria-hidden="true" /></span>
            <dl>
              <dt>Coach</dt>
              <dd>{coach}</dd>
              {coachEmail && <dd className={styles.contactLine}><Mail aria-hidden="true" /><a href={`mailto:${coachEmail}`}>{coachEmail}</a></dd>}
              {coachPhone && <dd className={styles.contactLine}><Phone aria-hidden="true" /><a href={`tel:${coachPhone.replace(/[^+\d]/g, '')}`}>{coachPhone}</a></dd>}
            </dl>
          </div>
        </section>
        <section className={`${styles.card} ${styles.modulesCard}`} aria-label={moduleLabel}>
          <div className={styles.moduleHeading}>
            <span className={styles.icon}><BookOpen aria-hidden="true" /></span>
            <h2>{moduleLabel}</h2>
            {!readOnlyModules && <Link to={allModulesHref} className={styles.viewAll}>View all<ArrowRight aria-hidden="true" /></Link>}
          </div>
          <div className={styles.moduleContent}>
            <div className={styles.learningArtwork} aria-hidden="true">
              <span /><span /><span /><GraduationCap />
            </div>
            {modules.length ? <ul className={styles.moduleList} aria-label={moduleLabel} tabIndex={0}>
              {modules.map(module => <li key={module.id}>
                {readOnlyModules ? <span className={styles.moduleItem} title={module.title}>
                  <span className={styles.moduleDot} aria-hidden="true" />
                  <span className={styles.moduleTitle}>{module.title}</span>
                </span> : <Link to={module.href} title={module.title}>
                  <span className={styles.moduleDot} aria-hidden="true" />
                  <span className={styles.moduleTitle}>{module.title}</span>
                  <ChevronRight aria-hidden="true" />
                </Link>}
              </li>)}
            </ul> : <p className={styles.modulePlaceholder} role="status">{modulePlaceholder}</p>}
          </div>
          <p className={styles.cardMotto}>Building your skills.</p>
        </section>
        {actionCards.length > 0 && <SeasonalHoverCards cards={actionCards} className={styles.actionCards} theme="hero" layout="inline" />}
      </div>
    </header>
  );
}
