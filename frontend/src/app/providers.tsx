import type { PropsWithChildren } from 'react';
import { Provider as JotaiProvider } from 'jotai';
import { useHydrateAtoms } from 'jotai/utils';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { queryClientAtom } from 'jotai-tanstack-query';
import { Toaster } from '~/components/ui/sonner';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

/**
 * Hydrate jotai's queryClientAtom with the SAME QueryClient the provider uses,
 * so jotai-tanstack-query atoms and `useQueryClient().invalidateQueries` share
 * one cache.
 */
function HydrateAtoms({ children }: PropsWithChildren) {
  useHydrateAtoms([[queryClientAtom, queryClient]]);
  return <>{children}</>;
}

export function Providers({ children }: PropsWithChildren) {
  return (
    <JotaiProvider>
      <QueryClientProvider client={queryClient}>
        <HydrateAtoms>{children}</HydrateAtoms>
        <Toaster />
      </QueryClientProvider>
    </JotaiProvider>
  );
}
