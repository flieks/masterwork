import type { PropsWithChildren } from 'react';
import { Provider as JotaiProvider } from 'jotai';
import { useHydrateAtoms } from 'jotai/utils';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { queryClientAtom } from 'jotai-tanstack-query';
import { MemoryRouter } from 'react-router-dom';

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false },
  },
});

function Hydrate({ children }: PropsWithChildren) {
  useHydrateAtoms([[queryClientAtom, client]]);
  return <>{children}</>;
}

/** Mirrors the app's providers so mounted components have jotai + query + router. */
export function TestProviders({ children }: PropsWithChildren) {
  return (
    <JotaiProvider>
      <QueryClientProvider client={client}>
        <Hydrate>
          <MemoryRouter>{children}</MemoryRouter>
        </Hydrate>
      </QueryClientProvider>
    </JotaiProvider>
  );
}
