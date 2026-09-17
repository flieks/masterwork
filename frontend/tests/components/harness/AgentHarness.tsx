import { useState, type PropsWithChildren } from 'react';
import { Provider as JotaiProvider } from 'jotai';
import { useHydrateAtoms } from 'jotai/utils';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { queryClientAtom } from 'jotai-tanstack-query';
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom';
import { Toaster } from '~/components/ui/sonner';
import { InstructionsPage } from '~/features/instructions';
import { AssistantAgentSwitcher } from '~/features/settings';
import { MessagePane } from '~/features/chat/components/MessagePane';

function Hydrate({ client, children }: PropsWithChildren<{ client: QueryClient }>) {
  useHydrateAtoms([[queryClientAtom, client]]);
  return <>{children}</>;
}

// A client per mount: settings are one shared key, and a cached agent from an earlier test would leak in.
function FreshQuery({ children }: PropsWithChildren) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      }),
  );
  return (
    <JotaiProvider>
      <QueryClientProvider client={client}>
        <Hydrate client={client}>
          {children}
          <Toaster />
        </Hydrate>
      </QueryClientProvider>
    </JotaiProvider>
  );
}

export function SwitcherApp() {
  return (
    <FreshQuery>
      <AssistantAgentSwitcher />
    </FreshQuery>
  );
}

export function MessagePaneApp({ sessionAgent }: { sessionAgent: string | null }) {
  return (
    <FreshQuery>
      <MemoryRouter>
        <MessagePane sessionId="chat-1" sessionAgent={sessionAgent} />
      </MemoryRouter>
    </FreshQuery>
  );
}

/** The page calls useBlocker, which needs a data router — not MemoryRouter. */
export function InstructionsApp({ path = '/instructions' }: { path?: string }) {
  const [router] = useState(() =>
    createMemoryRouter([{ path: '/instructions', element: <InstructionsPage /> }], {
      initialEntries: [path],
    }),
  );
  return (
    <FreshQuery>
      <RouterProvider router={router} />
    </FreshQuery>
  );
}
