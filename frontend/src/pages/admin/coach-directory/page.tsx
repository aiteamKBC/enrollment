import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Check, Copy, Link2, Pencil, Plus, Search, Trash2, Users } from 'lucide-react';
import styles from './directory.module.css';
import { AdminPage } from '../_shared/AdminPage';
import { bookingTypes, coachPagePath, deleteCoach, emptyLinks, listCoaches, saveCoach, type BookingLinks, type DirectoryCoach } from '@/api/coachBookingDirectory';

const button = styles.button;
const primary = `${styles.button} ${styles.primary}`;
export default function CoachDirectoryPage({ workspaceRole = "admin" }: { workspaceRole?: "admin" | "compliance" | "curriculum" }) {
  const [coaches, setCoaches] = useState<DirectoryCoach[]>([]);
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<{ id?: number; version?: number; name: string; links: BookingLinks } | null>(null);
  const [busy, setBusy] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const editorOpen = editing !== null;
  useEffect(() => { if (editorOpen) nameInput.current?.focus(); }, [editorOpen]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    listCoaches(controller.signal).then(data => setCoaches(data.coaches))
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [version]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (editing) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [editing]);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); if (!editing || busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await saveCoach(editing); setEditing(null); setVersion(value => value + 1); setNotice('Coach saved. The public booking page is up to date.'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save coach.'); }
    finally { setBusy(false); }
  }
  async function remove(coach: DirectoryCoach) {
    if (!window.confirm(`Permanently remove ${coach.name} from the booking directory? Their public LMS booking page will stop working. This does not delete their LMS account or cancel existing bookings.`)) return;
    setBusy(true); setError('');
    try { await deleteCoach(coach); setVersion(value => value + 1); setNotice('Coach removed from the directory.'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove coach.'); }
    finally { setBusy(false); }
  }
  async function copy(slug: string) {
    try { await navigator.clipboard.writeText(new URL(coachPagePath(slug), window.location.origin).href); setNotice('Public booking page link copied.'); }
    catch { setError('Could not copy the link. Open the booking page and copy its address.'); }
  }
  return <AdminPage workspaceRole={workspaceRole} title="Coach directory" subtitle="Manage coach booking pages" icon="ri-team-line" heroTitle="Coach directory"
    heroBlurb="Manage public booking links for each coach. Super admins, enrolment and curriculum staff can add, edit or remove coaches."
    stats={[{ label: 'Coaches', value: coaches.length }]}
    actions={<button className={primary} disabled={Boolean(editing) || busy} onClick={() => setEditing({ name: '', links: emptyLinks() })}><Plus size={18} aria-hidden="true" />Add coach</button>}>
    <div className={styles.directory}>
    {error && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-800">{error}<button className={`${button} ml-4`} disabled={busy} onClick={() => setVersion(value => value + 1)}>Reload directory</button></div>}
    {notice && <p role="status" className="rounded-xl bg-green-50 p-4 text-green-900">{notice}</p>}
    {editing && <form aria-label="Coach editor" onSubmit={submit} className={styles.editor}>
      <div className={styles.editorHeading}><span className={styles.headingIcon}><Pencil size={22} aria-hidden="true" /></span><h2>{editing.id ? 'Edit coach' : 'Add coach'}</h2></div>
      <p className="text-sm text-foreground-500">These details will be public. Leave unavailable session links blank.</p>
      <label className="block text-sm font-semibold">Coach name<input ref={nameInput} required maxLength={255} disabled={busy} value={editing.name} onChange={event => setEditing({ ...editing, name: event.target.value })} className="mt-2 block w-full rounded-xl border p-3" /></label>
      <div className="grid gap-5 md:grid-cols-2">{bookingTypes.map(type => <label key={type.key} className="block text-sm font-semibold">{type.label} URL<input type="url" maxLength={2048} disabled={busy} value={editing.links[type.key]} onChange={event => setEditing({ ...editing, links: { ...editing.links, [type.key]: event.target.value } })} placeholder="https://..." className="mt-2 block w-full rounded-xl border p-3" /></label>)}</div>
      <div className="flex justify-end gap-3"><button type="button" className={button} disabled={busy} onClick={() => { if (window.confirm('Discard changes to this coach?')) setEditing(null); }}>Cancel</button><button className={primary} disabled={busy}>{busy ? 'Saving...' : 'Save coach'}</button></div>
    </form>}
    <section className={styles.panel}>
      <div className={styles.toolbar}><div className={styles.sectionTitle}><span className={styles.headingIcon}><Users size={23} aria-hidden="true" /></span><div><h2>Coach booking pages</h2><p>Share a page, manage links and keep booking options up to date.</p></div></div><label className={styles.search}><Search size={18} aria-hidden="true" /><input aria-label="Search coaches" placeholder="Search coaches" value={search} onChange={event => setSearch(event.target.value)} /></label></div>
      {loading ? <p role="status" className={styles.empty}>Loading coaches...</p> : <div className={styles.grid}>{coaches.filter(coach => coach.name.toLowerCase().includes(search.toLowerCase())).map(coach => <article key={coach.id} className={styles.card}>
        <div className={styles.cardHeading}><span className={styles.avatar} aria-hidden="true">{coach.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('')}</span><div><h3>{coach.name}</h3><p>{bookingTypes.filter(type => coach.links[type.key]).length} booking options</p></div><span className={styles.publicBadge}>Public page</span></div>
        <div className={styles.pageAddress}><Link2 size={15} aria-hidden="true" /><span>{coachPagePath(coach.slug)}</span></div>
        <ul className={styles.sessions} aria-label={`Booking options for ${coach.name}`}>{bookingTypes.map(type => <li key={type.key} data-available={Boolean(coach.links[type.key])}><span>{coach.links[type.key] ? <Check size={13} aria-hidden="true" /> : <span aria-hidden="true">–</span>}</span>{type.label}<span className={styles.srOnly}>{coach.links[type.key] ? ': Available' : ': Not available'}</span></li>)}</ul>
        <Link className={`${primary} ${styles.openPage}`} to={coachPagePath(coach.slug)} target="_blank" rel="noopener noreferrer">Open booking page<ArrowUpRight size={18} aria-hidden="true" /></Link>
        <div className={styles.cardActions}><button className={button} onClick={() => void copy(coach.slug)}><Copy size={15} aria-hidden="true" />Copy page link</button><button className={button} disabled={busy || Boolean(editing)} onClick={() => { setEditing({ ...coach, links: { ...emptyLinks(), ...coach.links } }); setNotice(''); }}><Pencil size={15} aria-hidden="true" />Edit</button><button className={`${button} ${styles.remove}`} disabled={busy || Boolean(editing)} onClick={() => void remove(coach)}><Trash2 size={15} aria-hidden="true" />Remove</button></div>
      </article>)}</div>}
      {!loading && !coaches.some(coach => coach.name.toLowerCase().includes(search.toLowerCase())) && <p className={styles.empty}>No coaches found.</p>}
    </section>
    </div>
  </AdminPage>;
}
