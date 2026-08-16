import { useState } from 'react';
import { useAtom } from 'jotai';
import { SearchCheck } from 'lucide-react';
import type { FactoryRun } from '~/api/generated';
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
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { launchSessionMutationAtom } from '../queries';
import { StartedRunLink } from './StartedRunLink';

/** The scout stage answers questions about a repo; this is the question. */
function isItDoneQuestion(run: FactoryRun): string {
  return [
    'Is the work described below already implemented in this repository?',
    'Report what exists today, what is missing, and name the files that decide it.',
    '',
    run.request_text,
  ].join('\n');
}

/**
 * Asks the repo whether a run's work landed anyway, without rebuilding it: one
 * read-only `scout` stage instead of the plan-and-build a rerun would pay for.
 */
export function CheckDoneButton({ run }: { run: FactoryRun }) {
  const [{ mutateAsync: launch, isPending }] = useAtom(launchSessionMutationAtom);
  const [startedRunId, setStartedRunId] = useState<string | null>(null);

  async function check() {
    try {
      const started = await launch({
        project_path: run.project_path,
        request_text: isItDoneQuestion(run),
        workflow: 'scout',
        checks_run_id: run.run_id,
      });
      setStartedRunId(started.run_id ?? null);
      toast.success('Checking whether this is already done', {
        description: 'One read-only pass; its findings land on the new run.',
      });
    } catch (err) {
      toast.error('Could not start the check', { description: apiErrorMessage(err) });
    }
  }

  if (startedRunId) return <StartedRunLink runId={startedRunId} label="Checking" />;

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="shrink-0 text-xs text-muted-foreground"
          disabled={isPending}
          aria-label={`Check whether run ${run.run_id} is already done`}
        >
          <SearchCheck className="mr-1 size-3.5" />
          Check if done
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Check whether this work already landed?</AlertDialogTitle>
          <AlertDialogDescription>
            Runs one read-only scout stage: it reads the repo and reports what exists and what is
            missing. It writes nothing and builds nothing, so it costs a fraction of a rerun.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => void check()}>Check it</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
