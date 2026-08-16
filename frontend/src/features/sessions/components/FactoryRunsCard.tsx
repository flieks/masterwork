import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import type { FactoryRun } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { factoryRunTitle, runOutcomeMeta, sessionDetailPath } from '../runs';
import { factoryRunsQueryAtom } from '../queries';
import { RunActions } from './RunActions';

const SHOWN = 8;

/**
 * Runs still worth a decision: the head of each request's chain, unfinished.
 * A done run needs nothing, and a run someone already started again is that
 * newer run's business now — both fold away rather than crowding the list.
 */
function isOpen(run: FactoryRun): boolean {
  return run.outcome !== 'done' && run.superseded_by === null && !run.dismissed;
}

/**
 * The factory runs the run dirs record. Done runs are folded away by default —
 * what is left is what still wants a decision, each unresumable row saying why
 * rather than just missing its button.
 */
export function FactoryRunsCard() {
  const [{ data, isPending, isError }] = useAtom(factoryRunsQueryAtom);
  const [showDone, setShowDone] = useState(false);

  if (isPending || isError || !data || data.length === 0) return null;

  const open = data.filter(isOpen);
  const handledCount = data.length - open.length;
  const shown = (showDone ? data : open).slice(0, SHOWN);
  if (shown.length === 0 && handledCount === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Factory runs</CardTitle>
        <CardDescription>
          {open.length === 0
            ? 'Nothing needs a decision — every run finished, was started again, or was dismissed.'
            : "The run dirs' own records; a stopped or failed run resumes from its last committed stage."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col divide-y">
        {shown.map((run) => (
          <RunRow key={`${run.project_path}:${run.run_id}`} run={run} />
        ))}
        {handledCount > 0 ? (
          <button
            type="button"
            onClick={() => setShowDone((prev) => !prev)}
            className="self-start pt-2 text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            {showDone
              ? 'Hide handled runs'
              : `Show ${handledCount} handled run${handledCount === 1 ? '' : 's'}`}
          </button>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: FactoryRun }) {
  const meta = runOutcomeMeta(run);
  return (
    <div className="flex items-center gap-3 py-2">
      <Badge variant={meta.variant} className="w-20 justify-center">
        {meta.label}
      </Badge>
      {/* The run reports itself as a session too, under `factory-<run_id>`. */}
      <Link
        to={sessionDetailPath(`factory-${run.run_id}`)}
        className="min-w-0 flex-1 rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <p className="truncate text-sm" title={run.request_text}>
          {factoryRunTitle(run)}
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {run.project_name} · {run.run_id}
          {run.workflow && run.workflow !== 'full' ? ` · ${run.workflow}` : ''}
          {run.started_at ? (
            <span title={absoluteDateTime(run.started_at)}> · {relativeTime(run.started_at)}</span>
          ) : null}
          {run.reason ? ` · ${run.reason}` : ''}
        </p>
      </Link>
      <RunActions run={run} />
    </div>
  );
}
