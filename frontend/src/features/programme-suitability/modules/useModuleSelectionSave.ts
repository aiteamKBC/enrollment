import { useEffect, useRef, useState } from 'react';
import { useWizard } from '@/pages/users/wizard/WizardContext.helpers';
import type { ProgrammeModuleSelection } from './configuration';
import { ModuleSaveQueue, type ModuleSaveStatus } from './moduleSaveQueue';

export function useModuleSelectionSave() {
  const { draft, setSection, saveModules, hydrated, readOnly, modules, moduleSaveQueue } = useWizard();
  const [status, setStatus] = useState<ModuleSaveStatus>('idle');
  const [error, setError] = useState('');
  const save = useRef(saveModules);
  const refresh = useRef(modules?.retry);
  useEffect(() => { save.current = saveModules; }, [saveModules]);
  useEffect(() => { refresh.current = modules?.retry; }, [modules?.retry]);
  const [queue] = useState(() => moduleSaveQueue ?? new ModuleSaveQueue((selection) => save.current(selection), () => {}));
  useEffect(() => {
    const unsubscribe = queue.subscribe((next, failure) => {
      setStatus(next);
      setError(next === 'error' ? failure instanceof Error ? failure.message : 'Unable to save changes. Retry.' : '');
      if ((failure && typeof failure === 'object' && 'status' in failure && failure.status === 409)
        || (failure instanceof Error && /configuration version/i.test(failure.message))) refresh.current?.();
    });
    return () => { unsubscribe(); queue.flushOnLeave(); };
  }, [queue]);
  return { selection: draft.programmeModules, status, error,
    update: (next: ProgrammeModuleSelection) => {
      if (readOnly || !hydrated) return;
      setSection('programmeModules', next);
      queue.enqueue(next);
    },
    retry: () => queue.retry(),
  };
}
