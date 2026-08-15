import { useAtom } from 'jotai';
import { Play } from 'lucide-react';
import type { FactoryRun } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { factoryRunsQueryAtom, resumeFactoryRunMutationAtom } from '../queries';

const SHOWN = 8;

function stateBadge(run: FactoryRun): { label: string; variant: 'success' | 'destructive' | 'muted' | 'secondary' } {
  if (run.state === 'running') return { label: 'running', variant: 'secondary' };
  if (run.state === 'waiting_input') return { label: 'waiting', variant: 'secondary' };
  if (run.accepted) return { label: 'finished', variant: 'success' };
  if (run.state === 'stopped') return { label: 'stopped', variant: 'destructive' };
  return { label: run.state, variant: 'muted' };
}

/** First line of the request, which is all a row has room for. */
function requestTitle(run: FactoryRun): string {
  const line = run.request_text.split('\n', 1)[0];
  return line || run.run_id;
}

/**
 * Every factory run the projects' run dirs record, resumable ones with a
 * Resume button — a cost-cap stop or a crash is recoverable from here, no
 * terminal needed. Runs launched outside the UI are listed too.
 */
export function FactoryRunsCard() {
  const [{ data, isPending, isError }] = useAtom(factoryRunsQueryAtom);
  const [{ mutateAsync: resume, isPending: resuming, variables: resumingBody }] = useAtom(
    resumeFactoryRunMutationAtom,
  );

  if (isPending || isError || !data || data.length === 0) return null;

  async function resumeRun(run: FactoryRun) {
    try {
      await resume({ project_path: run.project_path, run_id: run.run_id });
      toast.success(`Run ${run.run_id} resumed`, { description: requestTitle(run) });
    } catch (err) {
      toast.error('Could not resume the run', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Factory runs</CardTitle>
        <CardDescription>
          The run dirs' own records; a stopped or crashed run resumes from its last committed
          stage.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col divide-y">
        {data.slice(0, SHOWN).map((run) => {
          const badge = stateBadge(run);
          const isResuming = resuming && resumingBody?.run_id === run.run_id;
          return (
            <div key={`${run.project_path}:${run.run_id}`} className="flex items-center gap-3 py-2">
              <Badge variant={badge.variant} className="w-20 justify-center">
                {badge.label}
              </Badge>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm" title={run.request_text}>
                  {requestTitle(run)}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {run.project_name} · {run.run_id}
                  {run.started_at ? (
                    <span title={absoluteDateTime(run.started_at)}>
                      {' '}
                      · {relativeTime(run.started_at)}
                    </span>
                  ) : null}
                  {run.reason ? ` · ${run.reason}` : ''}
                </p>
              </div>
              {run.resumable ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={resuming}
                  onClick={() => void resumeRun(run)}
                  aria-label={`Resume run ${run.run_id}`}
                >
                  <Play className="mr-1 size-3.5" />
                  {isResuming ? 'Resuming…' : 'Resume'}
                </Button>
              ) : null}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
