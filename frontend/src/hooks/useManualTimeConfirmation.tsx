import { useCallback, useEffect, useRef, useState } from 'react';
import { ManualTimeConfirmation } from '@/components/feature/ManualTimeConfirmation';

/** Only final submission waits for confirmation; draft saving stays independent. */
export function useManualTimeConfirmation() {
  const [duration, setDuration] = useState<number | string | null>(null);
  const pending = useRef<((confirmed: boolean) => void) | null>(null);
  useEffect(() => () => { pending.current?.(false); pending.current = null; }, []);
  const confirmTime = useCallback((value: number | string): Promise<boolean> => {
    if (pending.current || (typeof value === 'number' ? !Number.isFinite(value) || value < 0 : !value.trim())) return Promise.resolve(false);
    return new Promise(resolve => { pending.current = resolve; setDuration(value); });
  }, []);
  const finish = (confirmed: boolean) => {
    const resolve = pending.current;
    pending.current = null;
    setDuration(null);
    resolve?.(confirmed);
  };
  return {
    confirmTime,
    timeConfirmation: duration === null ? null : <ManualTimeConfirmation duration={duration}
      onConfirm={() => finish(true)} onEdit={() => finish(false)} />,
  };
}
