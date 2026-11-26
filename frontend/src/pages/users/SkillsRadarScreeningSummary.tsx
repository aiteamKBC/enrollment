import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { LearnerKind } from '@/api/extendedIlr';
import { getModuleImpact } from '@/features/programme-suitability/skills-radar/catalogue/api';
import { SCREENING_RULE_CHANGED } from '@/features/programme-suitability/skills-radar/catalogue/screeningRefresh';
import { screenedModules } from '@/features/programme-suitability/skills-radar/moduleImpact';

export function SkillsRadarScreeningSummary({ kind, learnerId, canReview }: {
  kind: LearnerKind; learnerId: string; canReview: boolean;
}) {
  const navigate = useNavigate();
  const [flags, setFlags] = useState<ReturnType<typeof screenedModules>>([]);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const reload = () => setRefresh(value => value + 1);
    window.addEventListener('focus', reload);
    window.addEventListener(SCREENING_RULE_CHANGED, reload);
    return () => {
      window.removeEventListener('focus', reload);
      window.removeEventListener(SCREENING_RULE_CHANGED, reload);
    };
  }, []);
  useEffect(() => {
    let active = true;
    setFlags([]); setError('');
    getModuleImpact(kind, learnerId).then(impact => {
      if (active) setFlags(screenedModules(impact));
    }).catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [kind, learnerId, refresh]);
  if (error) return <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">Could not load Skills Radar flags. {error}</p>;
  if (!flags.length) return null;
  return <section aria-label="Potential Prior Learning" className="rounded-xl border border-primary-200 bg-primary-50 p-4">
    <h3 className="font-semibold text-primary-900">Potential Prior Learning</h3>
    <p className="mt-1 text-sm text-foreground-700">Screening indication from distinct high-rated KSBs with verified Hard mappings. An assessor must review evidence before any RPL decision.</p>
    <ul className="mt-2 flex flex-wrap gap-2 text-sm">{flags.map(flag =>
      <li key={flag.id} className="rounded-lg border border-primary-200 bg-white px-3 py-1.5">{flag.name}: <strong>{flag.qualifyingCodes.length} qualifying KSBs</strong> ({flag.qualifyingCodes.join(', ')})</li>
    )}</ul>
    {canReview && <button type="button" className="mt-3 text-sm font-semibold text-primary-700 underline" onClick={() => navigate(`/users/${learnerId}?tab=suitability-rpl${kind === 'commercial' ? '&source=commercial' : ''}`)}>
      Open Suitability &amp; RPL review
    </button>}
  </section>;
}
