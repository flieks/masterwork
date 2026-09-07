import { useAtom } from 'jotai';
import { Card } from '~/components/ui/card';
import { Badge } from '~/components/ui/badge';
import { runForSessionQueryAtom } from '../queries';
import { factoryRunTitle, runOutcomeMeta } from '../runs';
import { RunActions } from './RunActions';

/**
 * The factory run this session was a stage of, with its Resume button — so a
 * failed run can be picked up from the session you are already looking at.
 * Renders nothing for a session no run owns (every plain chat session).
 */
export function SessionRunBanner({ sessionId }: { sessionId: string }) {
  const [{ data: run, isPending, isError }] = useAtom(runForSessionQueryAtom(sessionId));
  // A missing link is the common case, not a failure worth a banner of its own.
  if (isPending || isError || !run) return null;

  const meta = runOutcomeMeta(run);
  return (
    <Card className="flex flex-col gap-2 p-3">
      <div className="flex items-center gap-3">
        <Badge variant={meta.variant} className="w-20 justify-center">
          {meta.label}
        </Badge>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm" title={run.request_text}>
            {factoryRunTitle(run)}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            Factory run {run.run_id}
            {run.reason ? ` · ${run.reason}` : ''}
            {run.resume_hint ? ` · ${run.resume_hint}` : ''}
          </p>
        </div>
        <RunActions run={run} />
      </div>
      {/* A read-only run's whole point is what it concluded — printing the
          badge and hiding the answer would say nothing. */}
      {run.summary ? (
        <p className="whitespace-pre-wrap border-t pt-2 text-sm text-muted-foreground">
          {run.summary}
        </p>
      ) : null}
    </Card>
  );
}
