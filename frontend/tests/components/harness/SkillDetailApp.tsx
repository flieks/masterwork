import type { PropsWithChildren } from 'react';
import { Provider as JotaiProvider } from 'jotai';
import { useHydrateAtoms } from 'jotai/utils';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { queryClientAtom } from 'jotai-tanstack-query';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { Toaster } from '~/components/ui/sonner';
import { AssetDetailPage } from '~/features/assets/components/AssetDetailPage';

const client = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});

function Hydrate({ children }: PropsWithChildren) {
  useHydrateAtoms([[queryClientAtom, client]]);
  return <>{children}</>;
}

/** The detail page calls useBlocker, which needs a data router — not MemoryRouter. */
export function SkillDetailApp({ path }: { path: string }) {
  const router = createMemoryRouter(
    [{ path: '/skills/:name', element: <AssetDetailPage kind="skill" /> }],
    { initialEntries: [path] },
  );
  return (
    <JotaiProvider>
      <QueryClientProvider client={client}>
        <Hydrate>
          <RouterProvider router={router} />
          <Toaster />
        </Hydrate>
      </QueryClientProvider>
    </JotaiProvider>
  );
}
