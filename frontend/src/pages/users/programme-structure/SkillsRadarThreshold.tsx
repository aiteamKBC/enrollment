import { useContext, useEffect, useState } from 'react';
import { AuthContext } from '@/hooks/useAuth.helpers';
import { getScreeningSetting, saveScreeningSetting, type ScreeningSetting } from '@/features/programme-suitability/skills-radar/catalogue/api';

/** One provider-wide screening indicator; no learner or RPL record is written here. */
export function SkillsRadarThreshold() {
  const isAdmin = useContext(AuthContext)?.isAdmin ?? false;
  const [setting, setSetting] = useState<ScreeningSetting | null>(null);
  const [draft, setDraft] = useState('50');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!isAdmin) return;
    let active = true;
    setLoading(true);
    setError('');
    getScreeningSetting().then(value => {
      if (active) { setSetting(value); setDraft(String(value.thresholdPercent)); }
    }).catch((reason: Error) => { if (active) setError(reason.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [isAdmin, retry]);
  if (!isAdmin) return null;
  const save = async () => {
    if (!setting) return;
    const value = Number(draft);
    if (!Number.isInteger(value) || value < 1 || value > 100) {
      setError('Enter one whole-number threshold from 1 to 100%.'); return;
    }
    setSaving(true); setError('');
    try {
      const next = await saveScreeningSetting(value, setting.revision);
      setSetting(next); setDraft(String(next.thresholdPercent));
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the setting.'); }
    finally { setSaving(false); }
  };
  return <section aria-labelledby="screening-threshold-heading" className="rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5">
    <h2 id="screening-threshold-heading" className="text-base font-semibold">Skills Radar prior learning indicator</h2>
    <p className="mt-1 max-w-2xl text-sm text-foreground-600">One provider-defined screening threshold. A flag prompts an assessor review; it does not approve RPL or change training, funding or eligibility.</p>
    {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    {loading ? <p role="status" className="mt-3 text-sm">Loading setting…</p> : !setting
      ? <button type="button" className="mt-3 rounded-lg border border-background-300 px-4 py-2 text-sm font-semibold" onClick={() => setRetry(value => value + 1)}>Retry loading setting</button>
      : !setting.schemaReady
      ? <p role="status" className="mt-3 text-sm">The default is 50%. Apply the prepared migration 0020 before this setting can be edited.</p>
      : <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-sm font-medium">Weighted module readiness threshold (%)
          <input className="mt-1 block w-28 rounded-lg border border-background-300 px-3 py-2" type="number" min="1" max="100" step="1" value={draft} onChange={event => setDraft(event.target.value)} />
        </label>
        <button type="button" className="rounded-lg bg-primary-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" disabled={saving || draft === String(setting.thresholdPercent)} onClick={() => void save()}>{saving ? 'Saving…' : 'Save threshold'}</button>
      </div>}
  </section>;
}
