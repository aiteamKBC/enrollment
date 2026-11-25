import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { LearnerKind } from '@/api/extendedIlr';
import { getModuleImpact } from '@/features/programme-suitability/skills-radar/catalogue/api';
import { screenedModules } from '@/features/programme-suitability/skills-radar/moduleImpact';

export function SkillsRadarScreeningSummary({ kind, learnerId, canReview }: {
  kind: LearnerKind; learnerId: string; canReview: boolean;
}) {
  const navigate = useNavigate();
  const [flags, setFlags] = useState<ReturnType<typeof screenedModules>>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setFlags([]); setError('');
    getModuleImpact(kind, learnerId).then(impact => {
      if (active) setFlags(screenedModules(impact));
    }).catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [kind, learnerId]);
  if (error) return <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">Could not load Skills Radar flags. {error}</p>;
  if (!flags.length) return null;
  return <section aria-label="Potential Prior Learning" className="rounded-xl border border-primary-200 bg-primary-50 p-4">
    <h3 className="font-semibold text-primary-900">Potential Prior Learning</h3>
    <p className="mt-1 text-sm text-foreground-700">Screening indicator from complete self-assessments and verified module weights. An assessor must review evidence before any RPL decision.</p>
    <ul className="mt-2 flex flex-wrap gap-2 text-sm">{flags.map(flag =>
      <li key={flag.id} className="rounded-lg border border-primary-200 bg-white px-3 py-1.5">{flag.name}: <strong>{flag.score}%</strong></li>
    )}</ul>
    {canReview && <button type="button" className="mt-3 text-sm font-semibold text-primary-700 underline" onClick={() => navigate(`/users/${learnerId}?tab=suitability-rpl${kind === 'commercial' ? '&source=commercial' : ''}`)}>
      Open Suitability &amp; RPL review
    </button>}
  </section>;
}
