import { useAtom } from 'jotai';
import { Play } from 'lucide-react';
import type { FactoryRun } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { resumeFactoryRunMutationAtom } from '../queries';

/** Resumes one run; shared by the runs card and a session's own detail page. */
export function ResumeRunButton({ run }: { run: FactoryRun }) {
  const [{ mutateAsync: resume, isPending, variables }] = useAtom(resumeFactoryRunMutationAtom);
  const isThisRun = isPending && variables?.run_id === run.run_id;

  async function resumeRun() {
    try {
      await resume({ project_path: run.project_path, run_id: run.run_id });
      toast.success(`Run ${run.run_id} resumed`, {
        description: 'Picks up from the last committed stage, with no cost cap.',
      });
    } catch (err) {
      toast.error('Could not resume the run', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Button
      size="sm"
      variant="outline"
      className="shrink-0"
      disabled={isPending}
      onClick={() => void resumeRun()}
      aria-label={`Resume run ${run.run_id}`}
    >
      <Play className="mr-1 size-3.5" />
      {isThisRun ? 'Resuming…' : 'Resume'}
    </Button>
  );
}
