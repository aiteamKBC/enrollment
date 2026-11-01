import { useEffect, useRef, useState } from 'react';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import type { ProgrammeModuleSelection } from './configuration';

/** Autosave through the existing wizard draft API, serialising rapid changes. */
export function useModuleSelectionSave() {
  const { draft, setSection, saveIlr, hydrated, readOnly, modules } = useWizard();
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const pending = useRef<ProgrammeModuleSelection | undefined>(undefined);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const save = useRef(saveIlr);
  const refresh = useRef(modules?.retry);
  useEffect(() => { save.current = saveIlr; }, [saveIlr]);
  useEffect(() => { refresh.current = modules?.retry; }, [modules?.retry]);

  const selection = draft.programmeModules;
  useEffect(() => {
    // Hydration and a staff review must never overwrite a learner's selections.
    if (!hydrated || readOnly || !pending.current || pending.current !== selection) return;
    let active = true;
    const saveSnapshot = save.current;
    const timer = setTimeout(() => {
      queue.current = queue.current.catch(() => {}).then(async () => {
        try {
          await saveSnapshot();
          if (active) { setStatus('saved'); setError(''); }
        } catch (e) {
          if (e instanceof Error && /configuration version/i.test(e.message)) refresh.current?.();
          if (active) { setStatus('error'); setError(e instanceof Error ? e.message : 'Could not save module choices.'); }
        }
      });
    }, 200);
    return () => { active = false; clearTimeout(timer); };
  }, [selection, hydrated, readOnly, attempt]);

  return { selection, status, error,
    update: (next: ProgrammeModuleSelection) => {
      if (readOnly || !hydrated) return;
      pending.current = next; setStatus('saving'); setError(''); setSection('programmeModules', next);
    },
    retry: () => { setStatus('saving'); setError(''); setAttempt((n) => n + 1); },
  };
}
