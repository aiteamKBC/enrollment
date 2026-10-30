import { useState } from 'react';
import { AppIcon } from './AppIcon';
import type { AccountCheckAccount, AccountCheckStatus, SessionAccountCheck as AccountCheck } from '@/api/sessionResults';

const statusLabels: Record<AccountCheckStatus, string> = {
  matched: 'Matched',
  'different-account': 'Different account',
  'unverified-guest': 'Unverified guest',
  unknown: 'Unknown',
  unmatched: 'Unmatched participant',
};
const statusStyles: Record<AccountCheckStatus, string> = {
  matched: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  'different-account': 'bg-red-50 text-red-800 border-red-200',
  'unverified-guest': 'bg-amber-50 text-amber-900 border-amber-200',
  unknown: 'bg-slate-50 text-slate-700 border-slate-200',
  unmatched: 'bg-violet-50 text-violet-800 border-violet-200',
};
const order: AccountCheckStatus[] = ['different-account', 'unverified-guest', 'unknown', 'unmatched', 'matched'];

function StatusBadge({ status }: { status: AccountCheckStatus }) {
  return <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${statusStyles[status]}`}>{statusLabels[status]}</span>;
}

function accountLine(account: AccountCheckAccount) {
  if (account.email) return account.email;
  if (account.verification === 'unverified') return account.enteredEmail ? `Typed ${account.enteredEmail} (not verified)` : 'No Microsoft sign-in';
  if (account.verification === 'verified') return 'Microsoft did not share an email';
  return 'Account not reported';
}

const minutes = (seconds: number) => `${Math.floor(seconds / 60)}m ${seconds % 60}s`;

/** Staff-only comparison of the Microsoft account each participant joined with and their LMS email. */
export function SessionAccountCheck({ check }: { check: AccountCheck }) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<'attention' | 'all'>('attention');
  const attention = check.participants.filter(person => person.status !== 'matched');
  const shown = filter === 'all' ? check.participants : attention;
  if (!check.participants.length) return null;
  return <div className="rounded-xl border">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}
      className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left">
      <span className="font-semibold"><AppIcon className="ri-shield-user-line mr-2" />Account check — who joined with which Microsoft account</span>
      <span className="flex flex-wrap items-center gap-2">
        {order.filter(status => check.counts[status]).map(status =>
          <span key={status} className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusStyles[status]}`}>{check.counts[status]} {statusLabels[status].toLowerCase()}</span>)}
        <AppIcon className={open ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'} />
      </span>
    </button>
    {open && <div className="space-y-3 border-t p-4">
      <p className="text-sm text-foreground-600">Compares the Microsoft account Teams reported for each join with the email in the LMS. A matching name is never treated as proof. This check does not change attendance, hours or meeting settings.</p>
      {check.lookupPending > 0 && <p role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
        {check.lookupPending} signed-in {check.lookupPending === 1 ? 'account has' : 'accounts have'} no email yet. Use “Sync attendance &amp; files” to look {check.lookupPending === 1 ? 'it' : 'them'} up in the college directory.</p>}
      <label className="flex items-center gap-2 text-sm font-semibold">Show
        <select value={filter} onChange={event => setFilter(event.target.value as 'attention' | 'all')} className="rounded-lg border bg-white px-3 py-1.5 text-sm font-normal">
          <option value="attention">Needs attention ({attention.length})</option>
          <option value="all">Everyone ({check.participants.length})</option>
        </select>
      </label>
      {!shown.length ? <p className="text-sm text-foreground-500">Everyone who joined used their LMS account.</p>
        : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead><tr className="border-b text-foreground-500"><th className="p-3">Participant</th><th className="p-3">LMS email</th><th className="p-3">Microsoft account used</th><th className="p-3">Result</th></tr></thead>
          <tbody>{shown.map(person => <tr key={`${person.kind}:${person.expectedEmail || person.accounts[0]?.sourceRecordIds.join('|')}`} className="border-b align-top">
            <td className="p-3"><p className="font-semibold">{person.name}</p>
              <p className="text-xs text-foreground-500">{person.roles.length ? person.roles.join(', ') : 'Not on this session in the LMS'}{person.teamsRoles.length ? ` · Teams role: ${person.teamsRoles.join(', ')}` : ''}</p></td>
            <td className="p-3">{person.expectedEmail || <span className="text-foreground-500">No LMS person linked</span>}</td>
            <td className="p-3"><ul className="space-y-2">{person.accounts.map(account => <li key={account.sourceRecordIds.join('|')}>
              <p className="font-medium">{accountLine(account)}</p>
              <p className="text-xs text-foreground-500">{account.displayName ? `Teams name “${account.displayName}” · ` : ''}{account.accountType}{account.emailSource === 'directory' ? ' · email from college directory' : ''} · {account.joins} {account.joins === 1 ? 'join' : 'joins'}, {minutes(account.seconds)}</p>
              {person.accounts.length > 1 && <p className="mt-1"><StatusBadge status={account.status} /> <span className="text-xs">{account.reason}</span></p>}
            </li>)}</ul></td>
            <td className="p-3"><StatusBadge status={person.status} /><p className="mt-1 text-xs text-foreground-600">{person.reason}</p></td>
          </tr>)}</tbody>
        </table></div>}
    </div>}
  </div>;
}
