import { useContext, useEffect, useState } from 'react';
import { AuthContext } from '@/hooks/useAuth.helpers';
import { getScreeningSetting, saveScreeningSetting, type ScreeningSetting } from '@/features/programme-suitability/skills-radar/catalogue/api';
import type { ScreeningRule } from '@/features/programme-suitability/skills-radar/moduleImpact';

type Draft = { minimumQualifyingRating: string; maximumQualifyingRating: string; minimumHighRatedHardKsbs: string };
const fromSetting = (setting: ScreeningSetting): Draft => ({
  minimumQualifyingRating: String(setting.minimumQualifyingRating),
  maximumQualifyingRating: String(setting.maximumQualifyingRating),
  minimumHighRatedHardKsbs: String(setting.minimumHighRatedHardKsbs),
});
const wholeNumber = (value: string) => /^\d+$/.test(value) ? Number(value) : NaN;
function validate(draft: Draft): { rule: ScreeningRule | null; error: string } {
  const minimumQualifyingRating = wholeNumber(draft.minimumQualifyingRating);
  const maximumQualifyingRating = wholeNumber(draft.maximumQualifyingRating);
  const minimumHighRatedHardKsbs = wholeNumber(draft.minimumHighRatedHardKsbs);
  if (!Number.isInteger(minimumQualifyingRating) || minimumQualifyingRating < 1 || minimumQualifyingRating > 9)
    return { rule: null, error: 'Minimum qualifying rating must be a whole number from 1 to 9.' };
  if (!Number.isInteger(maximumQualifyingRating) || maximumQualifyingRating < 1 || maximumQualifyingRating > 9)
    return { rule: null, error: 'Maximum qualifying rating must be a whole number from 1 to 9.' };
  if (minimumQualifyingRating > maximumQualifyingRating)
    return { rule: null, error: 'Minimum qualifying rating must not exceed maximum qualifying rating.' };
  if (!Number.isInteger(minimumHighRatedHardKsbs) || minimumHighRatedHardKsbs < 1 || minimumHighRatedHardKsbs > 500)
    return { rule: null, error: 'Minimum qualifying Hard KSBs must be a whole number from 1 to 500.' };
  return { rule: { minimumQualifyingRating, maximumQualifyingRating, minimumHighRatedHardKsbs }, error: '' };
}

/** One provider-wide screening indicator; no learner or RPL record is written here. */
export function SkillsRadarThreshold() {
  const isAdmin = useContext(AuthContext)?.isAdmin ?? false;
  const [setting, setSetting] = useState<ScreeningSetting | null>(null);
  const [draft, setDraft] = useState<Draft>({ minimumQualifyingRating: '5', maximumQualifyingRating: '9', minimumHighRatedHardKsbs: '2' });
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!isAdmin) return;
    let active = true;
    setLoading(true);
    setError('');
    getScreeningSetting().then(value => {
      if (active) { setSetting(value); setDraft(fromSetting(value)); }
    }).catch((reason: Error) => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [isAdmin, retry]);
  if (!isAdmin) return null;
  const changed = Boolean(setting && Object.entries(draft).some(([key, value]) => value !== fromSetting(setting)[key as keyof Draft]));
  const { rule, error: validationError } = validate(draft);
  const change = (key: keyof Draft, value: string) => {
    setDraft(current => ({ ...current, [key]: value }));
    setError(''); setSuccess('');
  };
  const save = async () => {
    if (!setting || !setting.schemaReady || !changed || saving) return;
    if (!rule) { setError(validationError); return; }
    setSaving(true); setError('');
    try {
      const next = await saveScreeningSetting(rule, setting.revision);
      setSetting(next); setDraft(fromSetting(next)); setSuccess('Screening settings saved. Existing assessments will use the updated rule when refreshed.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the setting.'); }
    finally { setSaving(false); }
  };
  return <section aria-labelledby="screening-threshold-heading" className="rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5">
    <h2 id="screening-threshold-heading" className="text-base font-semibold">Skills Radar prior learning indicator</h2>
    <p className="mt-1 max-w-2xl text-sm text-foreground-600">Configure the provider's screening indication. An assessor must review evidence before any RPL decision; this setting does not change training, funding or eligibility.</p>
    {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    {success && <p role="status" className="mt-2 text-sm text-green-700">{success}</p>}
    {loading ? <p role="status" className="mt-3 text-sm">Loading setting…</p> : !setting
      ? <button type="button" className="mt-3 rounded-lg border border-background-300 px-4 py-2 text-sm font-semibold" onClick={() => setRetry(value => value + 1)}>Retry loading setting</button>
      : <>
        {!setting.schemaReady && <p role="status" className="mt-3 text-sm text-amber-800">Setup required: apply the prepared migrations 0021 and 0022 before editing these settings. The historical percentage setting remains stored separately.</p>}
        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          <label className="text-sm font-medium">Minimum qualifying rating
            <input className="mt-1 block w-full rounded-lg border border-background-300 bg-white px-3 py-2" type="number" min="1" max="9" step="1" value={draft.minimumQualifyingRating} disabled={!setting.schemaReady || saving} onChange={event => change('minimumQualifyingRating', event.target.value)} />
          </label>
          <label className="text-sm font-medium">Maximum qualifying rating
            <input className="mt-1 block w-full rounded-lg border border-background-300 bg-white px-3 py-2" type="number" min="1" max="9" step="1" value={draft.maximumQualifyingRating} disabled={!setting.schemaReady || saving} onChange={event => change('maximumQualifyingRating', event.target.value)} />
          </label>
          <label className="text-sm font-medium">Minimum qualifying Hard KSBs
            <input className="mt-1 block w-full rounded-lg border border-background-300 bg-white px-3 py-2" type="number" min="1" max="500" step="1" value={draft.minimumHighRatedHardKsbs} disabled={!setting.schemaReady || saving} onChange={event => change('minimumHighRatedHardKsbs', event.target.value)} />
          </label>
        </div>
        <p className="mt-3 text-sm text-foreground-700" aria-live="polite">{rule
          ? `Rule preview: flag a module when at least ${rule.minimumHighRatedHardKsbs} distinct verified Hard-mapped KSBs are rated ${rule.minimumQualifyingRating}–${rule.maximumQualifyingRating}.`
          : validationError}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" disabled={!setting.schemaReady || saving || !changed || !rule} onClick={() => void save()}>{saving ? 'Saving…' : 'Save Changes'}</button>
          {setting.schemaReady && <span className="text-sm text-foreground-600">{changed ? 'Unsaved changes' : 'All changes saved'}</span>}
          {error && <button type="button" className="text-sm font-semibold text-primary-700 underline" onClick={() => setRetry(value => value + 1)}>Reload current settings</button>}
        </div>
      </>}
  </section>;
}
