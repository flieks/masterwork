import { atomWithMutation, atomWithQuery, queryClientAtom } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import type { ObservabilityIntegration } from '~/api/generated';

export const INTEGRATIONS_QUERY_KEY = ['observabilityIntegrations'];

/**
 * Which coding agents are reporting their sessions here.
 *
 * Polled slowly rather than once: the state changes from outside the app —
 * an upgrade moves the forwarder, someone edits `settings.json` by hand — and
 * a stale "connected" would leave an empty Sessions tab with no explanation.
 */
export const integrationsQueryAtom = atomWithQuery(() => ({
  queryKey: INTEGRATIONS_QUERY_KEY,
  queryFn: async () => (await api.observability.listObservabilityIntegrations()).data,
  refetchInterval: 30_000,
}));

export const connectIntegrationMutationAtom = atomWithMutation((get) => ({
  mutationFn: (id: string): Promise<ObservabilityIntegration> =>
    api.observability.connectObservabilityIntegration(id).then((r) => r.data),
  onSuccess: () => get(queryClientAtom).invalidateQueries({ queryKey: INTEGRATIONS_QUERY_KEY }),
}));

export const disconnectIntegrationMutationAtom = atomWithMutation((get) => ({
  mutationFn: (id: string): Promise<ObservabilityIntegration> =>
    api.observability.disconnectObservabilityIntegration(id).then((r) => r.data),
  onSuccess: () => get(queryClientAtom).invalidateQueries({ queryKey: INTEGRATIONS_QUERY_KEY }),
}));

/** True when no agent is recording — the Sessions tab will stay empty until one is. */
export function isRecording(integrations: ObservabilityIntegration[] | undefined): boolean {
  return (integrations ?? []).some((i) => i.state === 'connected');
}
