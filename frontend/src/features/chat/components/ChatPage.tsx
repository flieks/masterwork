import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import type { ChatScope } from '../types';
import { GLOBAL_SCOPE } from '../types';
import { ChatWorkspace } from './ChatWorkspace';

/** Global (unscoped) chat at /chat and /chat/:sessionId. */
export function ChatPage() {
  const { sessionId } = useParams();

  const scope = useMemo<ChatScope>(
    () => ({
      listScope: GLOBAL_SCOPE,
      projectId: null,
      activeSessionId: sessionId,
      sessionHref: (id) => `/chat/${id}`,
      emptyHref: '/chat',
    }),
    [sessionId],
  );

  return <ChatWorkspace scope={scope} />;
}
