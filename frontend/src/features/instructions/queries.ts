import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import type { InstructionsDoc } from '~/api/generated';

export const INSTRUCTIONS_QUERY_KEY = ['instructions'];

/** The global `~/.claude/CLAUDE.md`. Resolves with `exists: false` when absent. */
export const instructionsQueryAtom = atomWithQuery(() => ({
  queryKey: INSTRUCTIONS_QUERY_KEY,
  queryFn: async () => (await api.instructions.getInstructions()).data,
}));

export const updateInstructionsMutationAtom = atomWithMutation(() => ({
  mutationFn: (content: string): Promise<InstructionsDoc> =>
    api.instructions.updateInstructions({ content }).then((r) => r.data),
}));
