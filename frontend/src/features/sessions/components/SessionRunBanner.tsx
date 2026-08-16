import { useAtom } from 'jotai';
import { Card } from '~/components/ui/card';
import { Badge } from '~/components/ui/badge';
import { runForSessionQueryAtom } from '../queries';
import { factoryRunTitle, runOutcomeMeta } from '../runs';
import { RerunRunButton } from './RerunRunButton';
import { ResumeRunButton } from './ResumeRunButton';

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
    <Card className="flex items-center gap-3 p-3">
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
        </p>
      </div>
      {run.resumable ? (
        <ResumeRunButton run={run} />
      ) : (
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs text-muted-foreground">{run.resume_hint}</span>
          {run.outcome === 'done' ? null : <RerunRunButton run={run} />}
        </div>
      )}
    </Card>
  );
}
