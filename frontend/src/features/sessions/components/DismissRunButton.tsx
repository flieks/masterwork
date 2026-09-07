import { useAtom } from 'jotai';
import { X } from 'lucide-react';
import type { FactoryRun } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { dismissFactoryRunMutationAtom } from '../queries';

/**
 * Waves a run out of the list without touching the run itself — nothing is
 * deleted, and the folded-away view offers it back.
 */
export function DismissRunButton({ run, dismissed }: { run: FactoryRun; dismissed: boolean }) {
  const [{ mutateAsync: setDismissed, isPending }] = useAtom(dismissFactoryRunMutationAtom);

  async function toggle() {
    try {
      await setDismissed({
        body: { project_path: run.project_path, run_id: run.run_id },
        dismissed,
      });
    } catch (err) {
      toast.error(
        dismissed ? 'Could not dismiss the run' : 'Could not bring the run back',
        { description: apiErrorMessage(err) },
      );
    }
  }

  if (!dismissed) {
    return (
      <Button
        size="sm"
        variant="ghost"
        className="shrink-0 text-xs text-muted-foreground"
        disabled={isPending}
        onClick={() => void toggle()}
      >
        Bring back
      </Button>
    );
  }

  return (
    <Button
      size="icon"
      variant="ghost"
      className="size-7 shrink-0 text-muted-foreground hover:text-foreground"
      disabled={isPending}
      onClick={() => void toggle()}
      aria-label={`Dismiss run ${run.run_id}`}
      title="Dismiss — keeps the run, drops it from this list"
    >
      <X className="size-4" />
    </Button>
  );
}
