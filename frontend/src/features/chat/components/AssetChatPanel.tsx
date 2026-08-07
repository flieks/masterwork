import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, MessageSquare, Plus, Trash2 } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { cn } from '~/lib/utils';
import {
  assetChatSessionsQueryAtom,
  createSessionMutationAtom,
  deleteSessionMutationAtom,
} from '../queries';
import { MessagePane } from './MessagePane';

interface AssetChatPanelProps {
  assetId: string;
  /** Used in the empty-state copy only. */
  kindLabel: string;
}

/**
 * Chat about one skill/agent, embedded in its detail page. Sessions are scoped
 * to the asset server-side, so the backend feeds Claude the file as context and
 * these chats never show up in the global chat list.
 */
export function AssetChatPanel({ assetId, kindLabel }: AssetChatPanelProps) {
  const [open, setOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  const [{ data: sessions, isPending }] = useAtom(assetChatSessionsQueryAtom(assetId));
  const [{ mutateAsync: create, isPending: creating }] = useAtom(createSessionMutationAtom);
  const [{ mutateAsync: remove }] = useAtom(deleteSessionMutationAtom);
  const queryClient = useQueryClient();

  function refreshSessions() {
    return queryClient.invalidateQueries({ queryKey: ['chatSessions'] });
  }

  async function newChat() {
    try {
      const session = await create({ assetId });
      await refreshSessions();
      setActiveId(session.id);
    } catch (err) {
      toast.error('Could not start chat', { description: apiErrorMessage(err) });
    }
  }

  // Opening the panel lands on the most recent chat for this asset, or opens a
  // fresh one. A never-used session is reused next time, so this can't pile up.
  useEffect(() => {
    if (!open || isPending || activeId) return;
    if (sessions && sessions.length > 0) setActiveId(sessions[0].id);
    else if (!creating) void newChat();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isPending, sessions, activeId]);

  async function deleteChat() {
    if (!activeId) return;
    try {
      await remove(activeId);
      queryClient.removeQueries({ queryKey: ['chatMessages', activeId] });
      await refreshSessions();
      setActiveId(null);
    } catch (err) {
      toast.error('Delete failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <section className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-accent/40"
      >
        <MessageSquare className="size-4 text-muted-foreground" />
        Chat about this {kindLabel}
        {sessions && sessions.length > 0 ? (
          <span className="text-xs font-normal text-muted-foreground">
            {sessions.length} chat{sessions.length === 1 ? '' : 's'}
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            'ml-auto size-4 text-muted-foreground transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>

      {open ? (
        <div className="border-t">
          <div className="flex items-center gap-2 border-b px-3 py-2">
            <select
              aria-label="Chat session"
              value={activeId ?? ''}
              onChange={(e) => setActiveId(e.target.value || null)}
              disabled={!sessions || sessions.length === 0}
              className="h-8 min-w-0 flex-1 truncate rounded-md border bg-background px-2 text-xs"
            >
              {(sessions ?? []).map((session) => (
                <option key={session.id} value={session.id}>
                  {session.title}
                </option>
              ))}
            </select>
            <Button size="sm" variant="outline" onClick={newChat} disabled={creating}>
              <Plus /> New
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={deleteChat}
              disabled={!activeId}
              aria-label="Delete this chat"
            >
              <Trash2 />
            </Button>
          </div>

          <div className="h-[32rem]">
            {activeId ? (
              <MessagePane key={activeId} sessionId={activeId} />
            ) : (
              <div className="space-y-3 p-4">
                <Skeleton className="h-16 w-2/3" />
                <Skeleton className="ml-auto h-12 w-1/2" />
              </div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
