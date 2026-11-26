import { CalendarCheck, CalendarDays, ChartNoAxesColumn, Layers, PanelsTopLeft, UserRound, Users } from 'lucide-react';
import { type DirectoryCoach, type BookingLinks } from '@/api/coachBookingDirectory';
import styles from './directory.module.css';
import { directoryStatistics } from './directoryData';

export function BookingOptionIcon({ type }: { type: keyof BookingLinks }) {
  const Icon = {
    first_session: CalendarDays,
    support_session: Users,
    coaching_session: Layers,
    progress_review: ChartNoAxesColumn,
    one_to_one: UserRound,
  }[type];
  return <Icon className={styles.optionIcon} size={17} aria-hidden="true" />;
}

export function DirectoryHero({ coaches }: { coaches: DirectoryCoach[] | null }) {
  const stats = directoryStatistics(coaches);
  const cards = [
    { label: 'Total coaches', value: stats.totalCoaches, icon: Users },
    { label: 'Public pages', value: stats.publicPages, icon: PanelsTopLeft },
    { label: 'Active booking options', value: stats.activeBookingOptions, icon: CalendarCheck },
    { label: 'Sessions offered', value: stats.sessionsOffered, icon: Layers },
  ];
  return <section className={styles.hero} aria-labelledby="coach-directory-hero-title">
    <svg className={styles.heroWaves} viewBox="0 0 1500 240" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="coach-wave"><stop stopColor="#f8f2fe" /><stop offset="1" stopColor="#c9a8e7" /></linearGradient></defs>
      <path fill="url(#coach-wave)" d="M0 240H1500V0C1170 -50 1050 0 920 80S630 210 0 240Z" />
      <path fill="#bd93da" fillOpacity=".22" d="M450 240C780 160 780 50 1050 65S1400 120 1500 0V240Z" />
      <path fill="none" stroke="#e5b744" strokeWidth="1.2" d="M480 170C650 160 650 20 770 65S900 45 980 0M750 240Q900 205 990 240" />
    </svg>
    <div className={styles.heroCopy}>
      <h2 id="coach-directory-hero-title">Organise coaching pages &amp; bookings</h2>
      <p>Manage public booking pages, session types, and coach availability<br className={styles.desktopBreak} /> for your coaching team — all in one place.</p>
      <p className={styles.permissionGuidance}>Super admins, enrolment and curriculum staff can add, edit or remove coaches.</p>
    </div>
    <img className={styles.heroIllustration} src="/images/coach-directory/coach-directory-hero.png" alt="" />
    <dl className={styles.statistics}>
      {cards.map(({ label, value, icon: Icon }) => <div key={label} className={styles.statCard}>
        <span className={styles.statIcon} aria-hidden="true"><Icon size={27} /></span>
        <div><dt>{label}</dt><dd title={label === 'Sessions offered' ? 'Session and calendar-slot totals are not provided by the directory API.' : undefined}>{value ?? '—'}</dd></div>
      </div>)}
    </dl>
  </section>;
}
