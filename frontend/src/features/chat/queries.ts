import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api, CHAT_TIMEOUT_MS } from '~/api/client';
import type { ChatSession, ChatExchange, Proposal } from '~/api/generated';

/**
 * Sessions for a scope. `scope` is the listChatSessions arg: 'none' for the
 * global chat (project_id IS NULL) or a project uuid. Keyed so global and each
 * project keep independent caches; `invalidateQueries(['chatSessions'])`
 * prefix-matches and refreshes every scope.
 */
export const chatSessionsQueryAtom = atomFamily((scope: string) =>
  atomWithQuery(() => ({
    queryKey: ['chatSessions', scope],
    queryFn: async () => (await api.chat.listChatSessions(scope)).data,
  })),
);

/** Sessions scoped to one skill/agent. Kept out of the global and project lists. */
export const assetChatSessionsQueryAtom = atomFamily((assetId: string) =>
  atomWithQuery(() => ({
    queryKey: ['chatSessions', 'asset', assetId],
    queryFn: async () => (await api.chat.listChatSessions(undefined, assetId)).data,
  })),
);

export const chatMessagesQueryAtom = atomFamily((sessionId: string) =>
  atomWithQuery(() => ({
    queryKey: ['chatMessages', sessionId],
    queryFn: async () => (await api.chat.listChatMessages(sessionId)).data,
    enabled: sessionId.length > 0,
  })),
);

export const createSessionMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: {
    title?: string | null;
    projectId?: string | null;
    assetId?: string | null;
  }): Promise<ChatSession> =>
    api.chat
      .createChatSession({
        title: vars.title ?? undefined,
        project_id: vars.projectId ?? undefined,
        asset_id: vars.assetId ?? undefined,
      })
      .then((r) => r.data),
}));

export const deleteSessionMutationAtom = atomWithMutation(() => ({
  mutationFn: (sessionId: string): Promise<void> =>
    api.chat.deleteChatSession(sessionId).then(() => undefined),
}));

export const sendMessageMutationAtom = atomWithMutation(() => ({
  // Long-running: a `claude -p` round trip can take minutes, so the client
  // timeout is disabled for this one call.
  mutationFn: (vars: { sessionId: string; content: string }): Promise<ChatExchange> =>
    api.chat
      .createChatMessage(vars.sessionId, { content: vars.content }, { timeout: CHAT_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const acceptProposalMutationAtom = atomWithMutation(() => ({
  mutationFn: (proposalId: string): Promise<Proposal> =>
    api.proposals.acceptProposal(proposalId).then((r) => r.data),
}));

export const rejectProposalMutationAtom = atomWithMutation(() => ({
  mutationFn: (proposalId: string): Promise<Proposal> =>
    api.proposals.rejectProposal(proposalId).then((r) => r.data),
}));
