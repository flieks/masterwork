import { useAtom } from 'jotai';
import { Link, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import type { ChatSession } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '~/components/ui/alert-dialog';
import { apiErrorMessage } from '~/api/client';
import { relativeTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import type { ChatScope } from '../types';
import {
  chatSessionsQueryAtom,
  createSessionMutationAtom,
  deleteSessionMutationAtom,
} from '../queries';

export function SessionSidebar({ scope }: { scope: ChatScope }) {
  const [{ data: sessions, isPending }] = useAtom(chatSessionsQueryAtom(scope.listScope));
  const [{ mutateAsync: create, isPending: creating }] = useAtom(createSessionMutationAtom);
  const [{ mutateAsync: remove }] = useAtom(deleteSessionMutationAtom);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  async function newChat() {
    try {
      const session = await create({ projectId: scope.projectId });
      queryClient.invalidateQueries({ queryKey: ['chatSessions'] });
      navigate(scope.sessionHref(session.id));
    } catch (err) {
      toast.error('Could not start chat', { description: apiErrorMessage(err) });
    }
  }

  async function deleteSession(id: string) {
    try {
      await remove(id);
      queryClient.invalidateQueries({ queryKey: ['chatSessions'] });
      queryClient.removeQueries({ queryKey: ['chatMessages', id] });
      if (id === scope.activeSessionId) navigate(scope.emptyHref);
    } catch (err) {
      toast.error('Delete failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r">
      <div className="p-3">
        <Button className="w-full" onClick={newChat} disabled={creating}>
          <Plus /> New chat
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {isPending ? (
          <div className="space-y-2 px-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : !sessions || sessions.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            No chats yet. Start one above.
          </p>
        ) : (
          <ul className="space-y-1">
            {sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                href={scope.sessionHref(session.id)}
                active={session.id === scope.activeSessionId}
                onDelete={() => deleteSession(session.id)}
              />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

function SessionRow({
  session,
  href,
  active,
  onDelete,
}: {
  session: ChatSession;
  href: string;
  active: boolean;
  onDelete: () => void;
}) {
  return (
    <li className="group relative">
      <Link
        to={href}
        className={cn(
          'block rounded-md px-2.5 py-2 pr-9 text-sm transition-colors',
          active ? 'bg-secondary text-secondary-foreground' : 'hover:bg-accent',
        )}
      >
        <div className="truncate font-medium">{session.title}</div>
        <div className="text-[11px] text-muted-foreground">{relativeTime(session.updated_at)}</div>
      </Link>

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <button
            type="button"
            aria-label={`Delete chat ${session.title}`}
            className="absolute right-1.5 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-background hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Trash2 className="size-4" />
          </button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              “{session.title}” and its messages will be permanently removed. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}
