import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api, GENERATE_TIMEOUT_MS } from '~/api/client';
import type {
  AutopilotCreateRequest,
  ScenarioGenerateResponse,
  Simulation,
  SimulationCreateRequest,
} from '~/api/generated';

const POLL_MS = 2500;

/** Project's simulations, newest first. Polls while any run is in flight. */
export const simulationsQueryAtom = atomFamily((projectId: string) =>
  atomWithQuery(() => ({
    queryKey: ['simulations', projectId],
    queryFn: async () => (await api.simulations.listSimulations(projectId)).data,
    enabled: projectId.length > 0,
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.status === 'running') ? POLL_MS : false,
    // Keep polling while the window is unfocused — a run takes minutes and the
    // user will usually be elsewhere when it finishes.
    refetchIntervalInBackground: true,
  })),
);

export const createSimulationMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { projectId: string; body: SimulationCreateRequest }): Promise<Simulation> =>
    api.simulations.createSimulation(vars.projectId, vars.body).then((r) => r.data),
}));

export const startAutopilotMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { projectId: string; body: AutopilotCreateRequest }): Promise<Simulation> =>
    api.simulations.startSimulationAutopilot(vars.projectId, vars.body).then((r) => r.data),
}));

export const stopAutopilotMutationAtom = atomWithMutation(() => ({
  mutationFn: (runId: string): Promise<void> =>
    api.simulations.stopSimulationAutopilot(runId).then(() => undefined),
}));

export const generateScenarioMutationAtom = atomWithMutation(() => ({
  // One-shot claude -p (~10–60 s) — no client timeout, same as chat/diagram generation.
  mutationFn: (projectId: string): Promise<ScenarioGenerateResponse> =>
    api.simulations
      .generateSimulationScenario(projectId, { timeout: GENERATE_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const applySuggestionMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { simulationId: string; index: number }): Promise<Simulation> =>
    api.simulations.applySimulationSuggestion(vars.simulationId, vars.index).then((r) => r.data),
}));

export const deleteSimulationMutationAtom = atomWithMutation(() => ({
  mutationFn: (simulationId: string): Promise<void> =>
    api.simulations.deleteSimulation(simulationId).then(() => undefined),
}));
