import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import type { AgentId, InstructionsDoc } from '~/api/generated';

export function instructionsQueryKey(agent: AgentId) {
  return ['instructions', agent];
}

/** One agent's global instructions file (CLAUDE.md or AGENTS.md); `exists: false` when absent. */
export const instructionsQueryAtom = atomFamily((agent: AgentId) =>
  atomWithQuery(() => ({
    queryKey: instructionsQueryKey(agent),
    queryFn: async () => (await api.instructions.getInstructions(agent)).data,
  })),
);

export const updateInstructionsMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { agent: AgentId; content: string }): Promise<InstructionsDoc> =>
    api.instructions.updateInstructions({ content: vars.content }, vars.agent).then((r) => r.data),
}));
