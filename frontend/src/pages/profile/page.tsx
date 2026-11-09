import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, CheckCircle2, ChevronRight, KeyRound, LoaderCircle, ShieldCheck, Signature, UserRound } from 'lucide-react';
import { Link } from 'react-router-dom';
import { apiChangePassword, describePasswordProblem } from '@/api/auth';
import { AccountSettingsApiError, fetchAccountSettings, fetchProfilePhoto, type LearnerAccountSettings } from '@/api/accountSettings';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { useAuth } from '@/hooks/useAuth.helpers';
import { roleNavMap } from '@/mocks/navigation';
import { SignatureSettings } from './SignatureSettings';
import styles from './accountSettings.module.css';

export type AccountSettingsView = 'home' | 'about' | 'signature' | 'security';
const viewCopy: Record<AccountSettingsView, { title: string; subtitle: string }> = {
  home: { title: 'Account Settings', subtitle: 'Manage your learner profile, signature sample and password' },
  about: { title: 'About You', subtitle: 'Review your personal, contact and residential details' },
  signature: { title: 'Signature Sample', subtitle: 'Manage the reusable signature offered when you choose to sign' },
  security: { title: 'Security Settings', subtitle: 'Change the password for your Enrolment Corner account' },
};

function initialsOf(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return 'U';
  return (parts.length === 1 ? parts[0].slice(0, 2) : `${parts[0][0]}${parts[parts.length - 1][0]}`).toUpperCase();
}

function displayDate(value: string) {
  if (!value) return 'Not recorded';
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
}

function BackToSettings() {
  return <Link to="/learner/account-settings" className={styles.backLink}><ArrowLeft size={17} aria-hidden="true" />Back to Account Settings</Link>;
}

function LoadingPanel() {
  return <div className={styles.loadingPanel}><LoaderCircle className={styles.spinner} size={22} aria-hidden="true" /><p>Loading your account settings…</p></div>;
}

function ErrorPanel({ error, onRetry }: { error: { message: string; status: number }; onRetry: () => void }) {
  return <div className={styles.errorPanel} role="alert"><p>{error.message}</p>{error.status === 401 ? <Link to="/login" className={styles.secondaryButton}>Sign in again</Link> : <button type="button" className={styles.secondaryButton} onClick={onRetry}>Try again</button>}</div>;
}

function SettingsHome() {
  const cards = [
    { to: '/learner/account-settings/about-you', icon: UserRound, title: 'About You', text: 'View your profile, contact and residential address details.' },
    { to: '/learner/account-settings/signature', icon: Signature, title: 'Signature Sample', text: 'Your reusable electronic signature sample.' },
    { to: '/learner/account-settings/security', icon: ShieldCheck, title: 'Security Settings', text: 'Change your account password securely.' },
  ];
  return <main className={styles.page}>
    <div className={styles.intro}><h2>Choose what you want to manage</h2><p>Your enrolment application, programme selection and signed document history are kept separate from these settings.</p></div>
    <nav className={styles.navCards} aria-label="Account settings sections">{cards.map(({ to, icon: Icon, title, text }) => <Link key={to} to={to} className={styles.navCard}><Icon size={21} strokeWidth={1.8} aria-hidden="true" /><span><strong>{title}</strong><small>{text}</small></span><ChevronRight size={19} aria-hidden="true" /></Link>)}</nav>
    <p className={styles.privacyNote}><ShieldCheck size={17} aria-hidden="true" />Identity changes that require verification remain protected and cannot be changed here.</p>
  </main>;
}

function Value({ label, value, protectedField = false }: { label: string; value?: string; protectedField?: boolean }) {
  return <div className={styles.value}><dt>{label}{protectedField && <span>Protected</span>}</dt><dd>{value || 'Not recorded'}</dd></div>;
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className={styles.field}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function SectionFrame({ id, title, description, children }: { id: string; title: string; description: string; children: ReactNode }) {
  return <section className={styles.settingsRow} aria-labelledby={id}><div className={styles.sectionIntro}><h2 id={id}>{title}</h2><p>{description}</p></div><div className={styles.panel}>{children}</div></section>;
}

function AboutYou({ data }: { data: LearnerAccountSettings }) {
  const [photoUrl, setPhotoUrl] = useState('');
  useEffect(() => {
    let active = true;
    let url = '';
    fetchProfilePhoto(data.learnerKind, data.learnerId).then((blob) => { if (!active || !blob) return; url = URL.createObjectURL(blob); setPhotoUrl(url); }).catch(() => {});
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [data.learnerId, data.learnerKind]);
  return <main className={styles.page}>
    <BackToSettings />
    <div className={styles.profileSummary}><div className={styles.largeAvatar}>{photoUrl ? <img src={photoUrl} alt="Your profile" /> : initialsOf(data.profile.fullName)}</div><div><h2>{data.profile.fullName || 'Learner'}</h2><p>{data.contact.emailAddress || 'No email recorded'}</p></div></div>
    <p className={styles.privacyNote}><ShieldCheck size={17} aria-hidden="true" />About You is read-only. Before signing, correct permitted details in the ILR step.</p>
    <SectionFrame id="profile-section" title="Profile" description="Your verified identity and the personal details used across enrolment.">
      <dl className={styles.valueGrid}><Value label="Title and full name" value={`${data.profile.title} ${data.profile.fullName}`.trim()} protectedField /><Value label="Pronouns" value={data.profile.pronouns} /><Value label="Post-nominals" value={data.profile.postNominals} /><Value label="Date of birth" value={displayDate(data.profile.dateOfBirth)} protectedField /></dl>
    </SectionFrame>
    <SectionFrame id="contact-section" title="Contact" description="How the enrolment team can contact you. Email changes require identity verification.">
      <dl className={styles.valueGrid}><Value label="Email address" value={data.contact.emailAddress} protectedField /><Value label="Mobile number" value={data.contact.mobileNumber} /><Value label="Landline number" value={data.contact.landlineNumber} /></dl>
    </SectionFrame>
    <SectionFrame id="address-section" title="Address" description="Your residential address. This remains separate from your employer's address.">
      <dl className={styles.valueGrid}><Value label="Address line 1" value={data.address.addressLine1} /><Value label="Address line 2" value={data.address.addressLine2} /><Value label="Address line 3" value={data.address.addressLine3} /><Value label="Address line 4" value={data.address.addressLine4} /><Value label="Residential postcode" value={data.address.residentialPostcode} /><Value label="Country" value={data.address.country} /></dl>
    </SectionFrame>
  </main>;
}

function SecuritySettings({ email }: { email: string }) {
  const [current, setCurrent] = useState(''); const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false); const [message, setMessage] = useState(''); const [error, setError] = useState('');
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(''); setMessage('');
    const problem = describePasswordProblem(password, email);
    if (!current) { setError('Enter your current password.'); return; }
    if (problem) { setError(problem); return; }
    if (password !== confirm) { setError('The new passwords do not match.'); return; }
    setSaving(true);
    try { await apiChangePassword(current, password); setCurrent(''); setPassword(''); setConfirm(''); setMessage('Your password has been changed. You remain signed in on this device.'); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Your password could not be changed.'); }
    finally { setSaving(false); }
  };
  return <main className={styles.page}><BackToSettings /><section className={styles.settingsRow} aria-labelledby="password-title"><div className={styles.sectionIntro}><h2 id="password-title">Change password</h2><p>Confirm your current password, then choose a new one. Other signed-in sessions are ended for your security.</p></div><form className={styles.panel} onSubmit={submit}><div className={styles.formGrid}><Field label="Current password"><input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} /></Field><Field label="New password" hint="Use at least 8 characters. Avoid common passwords and your email address."><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} /></Field><Field label="Confirm new password"><input type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} /></Field>{error && <p className={styles.error} role="alert">{error}</p>}{message && <p className={styles.success} role="status"><CheckCircle2 size={17} aria-hidden="true" />{message}</p>}<div className={styles.formActions}><button type="submit" className={styles.primaryButton} disabled={saving}>{saving ? <LoaderCircle size={16} className={styles.spinner} /> : <KeyRound size={16} />}{saving ? 'Changing…' : 'Change password'}</button></div></div></form></section></main>;
}

function GenericAccount() {
  const { auth } = useAuth();
  return <main className={styles.page}><div className={styles.panel}><h2>Account details</h2><dl className={styles.valueGrid}><Value label="Name" value={auth.account?.displayName || ''} /><Value label="Email" value={auth.account?.email || ''} protectedField /></dl></div></main>;
}

export default function ProfilePage({ view = 'home' }: { view?: AccountSettingsView }) {
  const { auth } = useAuth();
  const account = auth.account;
  const isLearner = account?.subjectType === 'learner' && Boolean(account.subjectId);
  const role = account?.role === 'learner' ? 'learner' : 'compliance';
  const nav = roleNavMap[role] || roleNavMap.compliance;
  const copy = viewCopy[view];
  const [data, setData] = useState<LearnerAccountSettings | null>(null);
  const [loading, setLoading] = useState(view === 'about');
  const [error, setError] = useState<{ message: string; status: number } | null>(null);
  const requestId = useRef(0);
  const learnerId = isLearner ? account?.subjectId : null;
  const load = useCallback(() => {
    if (!learnerId || view !== 'about') return;
    const currentRequest = ++requestId.current;
    setData(null); setLoading(true); setError(null);
    fetchAccountSettings(learnerId)
      .then((next) => { if (currentRequest === requestId.current) setData(next); })
      .catch((reason) => { if (currentRequest === requestId.current) setError({ message: reason instanceof AccountSettingsApiError ? reason.message : 'Your account settings could not be loaded. Please try again.', status: reason instanceof AccountSettingsApiError ? reason.status : 0 }); })
      .finally(() => { if (currentRequest === requestId.current) setLoading(false); });
  }, [learnerId, view]);
  useEffect(() => { load(); return () => { requestId.current += 1; }; }, [load]);
  return <WorkspaceShell role={role} roleLabel={nav.label} navItems={nav.items} pageTitle={isLearner ? copy.title : 'My profile'} pageSubtitle={isLearner ? copy.subtitle : 'Your signed-in account'}>{!isLearner ? <GenericAccount /> : view === 'home' ? <SettingsHome /> : view === 'about' ? (loading ? <LoadingPanel /> : error ? <ErrorPanel error={error} onRetry={load} /> : data ? <AboutYou data={data} /> : null) : view === 'signature' ? <main className={styles.page}><BackToSettings /><SignatureSettings accountKey={`${account.subjectType}:${account.subjectId}`} /></main> : <SecuritySettings email={account.email} />}</WorkspaceShell>;
}
