import { useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, ChevronDown, ChevronRight, MessageSquare } from 'lucide-react';
import type { WorkPrThread } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { prThreadsQueryAtom } from '../queries';

interface PullRequestThreadsProps {
  prId: number;
}

interface ThreadGroup {
  filePath: string | null;
  threads: WorkPrThread[];
}

/** PR-level threads (no file_path) first, then file groups sorted by path;
 * within a group, threads keep the order the backend returned them in. */
function groupThreads(threads: WorkPrThread[]): ThreadGroup[] {
  const prLevel = threads.filter((t) => t.file_path === null);
  const byFile = new Map<string, WorkPrThread[]>();
  for (const thread of threads) {
    if (thread.file_path === null) continue;
    const bucket = byFile.get(thread.file_path) ?? [];
    bucket.push(thread);
    byFile.set(thread.file_path, bucket);
  }
  const fileGroups: ThreadGroup[] = [...byFile.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([filePath, fileThreads]) => ({ filePath, threads: fileThreads }));
  return prLevel.length > 0 ? [{ filePath: null, threads: prLevel }, ...fileGroups] : fileGroups;
}

export function PullRequestThreads({ prId }: PullRequestThreadsProps) {
  const [{ data, isPending, isError, error, refetch }] = useAtom(prThreadsQueryAtom(prId));

  if (isPending) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        icon={<AlertTriangle className="size-6" />}
        title="Couldn't load review comments"
        description={apiErrorMessage(error)}
        action={
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Retry
          </Button>
        }
      />
    );
  }

  if (data.length === 0) {
    return <EmptyState icon={<MessageSquare className="size-6" />} title="No review threads" />;
  }

  return (
    <div className="flex flex-col gap-3">
      {groupThreads(data).map((group) => (
        <div key={group.filePath ?? '__pr__'} className="flex flex-col gap-1.5">
          <p className="font-mono text-xs text-muted-foreground">
            {group.filePath ?? 'On the pull request'}
          </p>
          {group.threads.map((thread) => (
            <ThreadCard key={thread.id} thread={thread} />
          ))}
        </div>
      ))}
    </div>
  );
}

function ThreadCard({ thread }: { thread: WorkPrThread }) {
  // Resolved threads start collapsed and de-emphasised; an open one is the
  // point of the view, so it starts expanded.
  const [open, setOpen] = useState(!thread.is_resolved);

  return (
    <div className={`rounded-md border ${thread.is_resolved ? 'bg-muted/30 opacity-70' : 'bg-card'}`}>
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} thread ${thread.external_id}`}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <span>
          {thread.right_file_line !== null
            ? `Line ${thread.right_file_line}`
            : `Thread #${thread.external_id}`}
        </span>
        {thread.is_resolved ? <Badge variant="muted">Resolved</Badge> : null}
      </button>
      {open ? (
        <ul className="flex flex-col gap-2 px-3 pb-3">
          {thread.comments.map((comment, i) => (
            <li key={comment.id ?? i} className="text-sm">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{comment.author ?? 'Unknown'}</span>
                {comment.published_at ? (
                  <time
                    dateTime={comment.published_at}
                    title={absoluteDateTime(comment.published_at)}
                  >
                    {relativeTime(comment.published_at)}
                  </time>
                ) : null}
              </div>
              {/* DevOps-authored text — plain, never markdown/HTML-rendered. */}
              <p className="whitespace-pre-wrap break-words">{comment.content}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
