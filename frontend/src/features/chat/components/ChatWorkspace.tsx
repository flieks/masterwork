import { useEffect } from 'react';
import { useAtom } from 'jotai';
import { useNavigate } from 'react-router-dom';
import { MessageSquare } from 'lucide-react';
import { EmptyState } from '~/components/EmptyState';
import type { ChatScope } from '../types';
import { chatSessionsQueryAtom } from '../queries';
import { SessionSidebar } from './SessionSidebar';
import { MessagePane } from './MessagePane';

/**
 * The full chat surface (sessions sidebar + message pane), driven entirely by a
 * ChatScope. The global chat and every project chat render this same component
 * with a different scope — the UI is shared, never forked.
 */
export function ChatWorkspace({ scope }: { scope: ChatScope }) {
  const [{ data: sessions }] = useAtom(chatSessionsQueryAtom(scope.listScope));
  const navigate = useNavigate();

  // No session in the URL yet: land on the most recently active one instead of an empty pane.
  useEffect(() => {
    if (!scope.activeSessionId && sessions && sessions.length > 0) {
      navigate(scope.sessionHref(sessions[0].id), { replace: true });
    }
  }, [scope, sessions, navigate]);

  return (
    <div className="flex h-full min-h-0">
      <SessionSidebar scope={scope} />
      <div className="flex min-w-0 flex-1 flex-col">
        {scope.activeSessionId ? (
          <MessagePane key={scope.activeSessionId} sessionId={scope.activeSessionId} />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              icon={<MessageSquare className="size-8" />}
              title="No chat selected"
              description="Start a new chat or pick one from the sidebar."
              className="max-w-sm"
            />
          </div>
        )}
      </div>
    </div>
  );
}
