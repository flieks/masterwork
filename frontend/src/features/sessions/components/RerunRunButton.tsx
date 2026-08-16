import { useState } from 'react';
import { useAtom } from 'jotai';
import { RotateCcw } from 'lucide-react';
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

/**
 * Starts the run's request over as a new run — the way out for a run nothing
 * can resume, since a branch that moved on can never be resumed onto.
 * Behind a confirm: a run spends money and this one carries no cost cap.
 */
export function RerunRunButton({ run }: { run: FactoryRun }) {
  const [{ mutateAsync: launch, isPending }] = useAtom(launchSessionMutationAtom);
  // Holds until the refetched run carries `superseded_by` and this button
  // is replaced by the link to the new run — no window for a second click.
  const [started, setStarted] = useState(false);

  async function rerun() {
    try {
      await launch({
        project_path: run.project_path,
        request_text: run.request_text,
        mode: run.interview ? 'interview' : 'autonomous',
      });
      setStarted(true);
      toast.success('New run started', { description: 'It picks up from planning, uncapped.' });
    } catch (err) {
      toast.error('Could not start the run', { description: apiErrorMessage(err) });
    }
  }

  if (started) {
    return (
      <Button size="sm" variant="outline" className="shrink-0" disabled>
        Started
      </Button>
    );
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          disabled={isPending}
          aria-label={`Run ${run.run_id} again`}
        >
          <RotateCcw className="mr-1 size-3.5" />
          Run again
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Start this request as a new run?</AlertDialogTitle>
          <AlertDialogDescription>
            Nothing about {run.run_id} is reused — the new run plans from scratch, on the current
            code, with no cost cap. The old run stays as it is.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={() => void rerun()}>Start it</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
