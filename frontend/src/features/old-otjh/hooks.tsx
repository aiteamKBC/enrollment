import { useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LearnerEntryGate } from './LearnerEntryGate';

export function OldOtjhProvider({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: {
    retry: false, staleTime: 2000, refetchOnWindowFocus: true, gcTime: 120000,
  } } }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export function OldOtjhGate({ children }: { children: ReactNode }) {
  return <LearnerEntryGate>{children}</LearnerEntryGate>;
}
