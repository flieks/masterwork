import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, GitBranch } from 'lucide-react';
import { Skeleton } from '~/components/ui/skeleton';
import { apiErrorMessage } from '~/api/client';
import { runDuration } from '~/lib/timeline';
import { childSessionsQueryAtom, runTitleMeta, sessionDetailPath } from '../queries';
import { stageRunsLabel } from '../runs';
import { RunStatusChip } from './RunStatusChip';

/**
 * Each stage of a pipeline is its own headless run. They are hidden from the
 * grid so five children don't bury their parent — this is how you get to them.
 * The list is only fetched once it is opened.
 */
export function ChildRuns({ sessionId, childCount }: { sessionId: string; childCount: number }) {
  const [open, setOpen] = useState(false);
  if (childCount === 0) return null;

  return (
    <section className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 p-2.5 text-sm transition-colors hover:bg-accent/40"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <GitBranch className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <span className="font-medium">{stageRunsLabel(childCount)}</span>
        <span className="text-xs text-muted-foreground">launched by this run</span>
      </button>
      {open ? <ChildList sessionId={sessionId} /> : null}
    </section>
  );
}

function ChildList({ sessionId }: { sessionId: string }) {
  const [{ data, isPending, isError, error }] = useAtom(childSessionsQueryAtom(sessionId));

  if (isPending) return <Skeleton className="m-2.5 h-16" />;
  if (isError) {
    return <p className="p-2.5 pt-0 text-xs text-muted-foreground">{apiErrorMessage(error)}</p>;
  }
  if (data.length === 0) {
    return (
      <p className="p-2.5 pt-0 text-xs text-muted-foreground">
        The stage runs were not recorded separately.
      </p>
    );
  }

  return (
    <ul className="divide-y border-t">
      {data.map((child) => {
        const title = runTitleMeta(child);
        return (
          <li key={child.id}>
            <Link
              to={sessionDetailPath(child.id)}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 p-2.5 transition-colors hover:bg-accent/40"
            >
              <span className="min-w-0 flex-1 truncate text-sm">{title.text}</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {runDuration(child.active_ms, child.wall_ms).active} active
              </span>
              <RunStatusChip status={child.status} />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
