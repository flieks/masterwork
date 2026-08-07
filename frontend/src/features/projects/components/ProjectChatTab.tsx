import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ChatScope } from '~/features/chat';
import { ChatWorkspace } from '~/features/chat';
import { projectDetailPath } from '../queries';

/**
 * Project-scoped chat. Reuses the shared ChatWorkspace with a project scope:
 * sessions are listed/created for this project, and the active session lives in
 * the `?session=` search param so it stays deep-linkable within the tab.
 */
export function ProjectChatTab({ projectId }: { projectId: string }) {
  const [searchParams] = useSearchParams();
  const activeSessionId = searchParams.get('session') ?? undefined;
  const base = projectDetailPath(projectId);

  const scope = useMemo<ChatScope>(
    () => ({
      listScope: projectId,
      projectId,
      activeSessionId,
      sessionHref: (id) => `${base}?tab=chat&session=${id}`,
      emptyHref: `${base}?tab=chat`,
    }),
    [projectId, activeSessionId, base],
  );

  return <ChatWorkspace scope={scope} />;
}
