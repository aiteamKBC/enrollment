import { useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { ArrowRight, CalendarDays, CheckCircle2, ExternalLink, LifeBuoy, MessagesSquare, TrendingUp, UserRound } from 'lucide-react';
import styles from './booking.module.css';

const icons = [CalendarDays, LifeBuoy, MessagesSquare, TrendingUp, UserRound];
import { bookingTypes, getPublicCoach, type PublicCoach } from '@/api/coachBookingDirectory';

export default function CoachBookingPage() {
  const { slug = '' } = useParams();
  // Links in emails (e.g. the learner invitation) can open a booking type directly.
  const requested = useSearchParams()[0].get('session') ?? '';
  const [coach, setCoach] = useState<PublicCoach | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setCoach(null); setSelected(''); setError(''); setLoading(true);
    getPublicCoach(slug, controller.signal).then(found => {
      setCoach(found);
      const wanted = bookingTypes.find(type => type.key === requested);
      if (wanted && found.links[wanted.key]) setSelected(wanted.key);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [slug, reload, requested]);
  const safe = (url: string) => { try { const parsed = new URL(url); return ['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password; } catch { return false; } };
  const active = bookingTypes.find(type => type.key === selected);
  const url = active && coach ? coach.links[active.key] : '';
  const embed = url && safe(url) && new URL(url).origin !== window.location.origin;
  return <main className={styles.page}>
    <div className={styles.container}>
      <div className={styles.brandBar}><img src="/assets/kbc-logo.png" alt="Kent Business College" width="175" height="80" /><span><CalendarDays size={17} aria-hidden="true" />Coach bookings</span></div>
      <header className={styles.hero}>
        <div className={styles.heroCopy}><p className={styles.eyebrow}>YOUR NEXT STEP STARTS HERE</p><h1>{coach ? `Book a session with ${coach.name}` : 'Coach bookings'}</h1><p>Make time for your learning. Choose the support you need and find a time that works for you.</p><span className={styles.accessNote}><CheckCircle2 size={16} aria-hidden="true" />No LMS account needed to view booking options</span></div>
        <div className={styles.heroIcon} aria-hidden="true"><CalendarDays strokeWidth={1.3} /></div>
      </header>
      {loading && <p role="status" className={styles.message}>Loading booking options...</p>}
      {error && <div role="alert" className={styles.message}>{error}<button className={styles.retry} onClick={() => setReload(value => value + 1)}>Retry</button></div>}
      {coach && <>
      <section aria-label="Booking options">
        <div className={styles.sectionHeading}><span className={styles.stepNumber}>01</span><div><h2>Choose your session</h2><p>Select the type of conversation you would like to have.</p></div></div>
        <div className={styles.options}>{bookingTypes.map((type, index) => {
          const available = Boolean(coach.links[type.key]) && safe(coach.links[type.key]);
          const chosen = selected === type.key;
          const Icon = icons[index];
          return <button key={type.key} disabled={!available} aria-pressed={chosen} onClick={() => setSelected(type.key)} className={styles.option}>
            <span className={styles.cardTop}><span className={styles.optionIcon}><Icon size={24} strokeWidth={1.7} aria-hidden="true" /></span>{chosen && <CheckCircle2 size={20} aria-hidden="true" />}</span>
            <span className={styles.optionTitle}>{type.label}</span><span className={styles.optionDescription}>{type.description}</span>
            <span className={styles.optionAction}>{available ? chosen ? 'Selected' : 'Choose session' : 'Not available'}{available && <ArrowRight size={17} aria-hidden="true" />}</span>
          </button>;
        })}</div>
      </section>
      <section className={styles.booking} aria-label="Session booking">
        {url && safe(url) ? <><div className={styles.bookingHeader}><div className={styles.sectionHeading}><span className={styles.stepNumber}>02</span><div><h2>{active?.label}</h2><p>With {coach.name}. Select an available date and time below.</p></div></div><a href={url} target="_blank" rel="noopener noreferrer" className={styles.openBooking}>Open booking in new tab<ExternalLink size={17} aria-hidden="true" /></a></div>
          <p className={styles.bookingNote}>If the booking form does not load, use the button above to continue in a new tab.</p>
          {embed && <iframe key={url} src={url} title={`${active?.label} booking with ${coach.name}`} className={styles.frame} referrerPolicy="no-referrer" sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation" />}</>
          : <div className={styles.empty}><span className={styles.emptyIcon}><CalendarDays size={30} aria-hidden="true" /></span><h2>Choose a booking option above</h2><p>Your coach's available times will appear here once you select a session.</p></div>}
      </section></>}
      <footer className={styles.footer}><span>Kent Business College</span><span>Learning support, one conversation at a time.</span></footer>
    </div>
  </main>;
}
