import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, ShieldCheck, Sparkles } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { auditGeneralityMutationAtom } from '../queries';

/** Generality tab: audits the linked skills/agents for scenario-specific
 * leakage — a past scenario's domain baked into shared, reusable assets. The
 * read-side counterpart to the anti-overfitting guard in the simulation prompt.
 * Persisted on the project, so it survives reloads. */
export function ProjectGeneralityTab({ project }: { project: Project }) {
  const [{ mutateAsync: audit, isPending }] = useAtom(auditGeneralityMutationAtom);
  const queryClient = useQueryClient();

  async function handleAudit() {
    try {
      await audit(project.id);
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
      toast.success('Generality audit complete');
    } catch (err) {
      toast.error('Could not audit generality', { description: apiErrorMessage(err) });
    }
  }

  const auditButton = (
    <Button size="sm" onClick={handleAudit} disabled={isPending}>
      {isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
      {isPending ? 'Auditing…' : project.generality_report ? 'Re-audit' : 'Audit'}
    </Button>
  );

  return (
    <div className="mx-auto w-full max-w-4xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Generality audit</h2>
          <p className="text-sm text-muted-foreground">
            Checks whether your linked skills and agents stayed general — or whether a past
            scenario&apos;s domain leaked into what should be reusable assets.
          </p>
        </div>
        {auditButton}
      </div>

      {isPending && (
        <p className="mt-4 text-xs text-muted-foreground">
          Claude is reading every linked asset — this takes up to a minute.
        </p>
      )}

      {project.generality_report ? (
        <div className="mt-6 space-y-3">
          {project.generality_report_at && (
            <p
              className="text-xs text-muted-foreground"
              title={absoluteDateTime(project.generality_report_at)}
            >
              Audited {relativeTime(project.generality_report_at)}
            </p>
          )}
          <MarkdownView content={project.generality_report} />
        </div>
      ) : (
        !isPending && (
          <EmptyState
            className="mt-16"
            icon={<ShieldCheck className="size-8" />}
            title="No audit yet"
            description="Run an audit to check that your skills and agents stayed general — and catch any scenario-specific logic that leaked into a shared asset."
          />
        )
      )}
    </div>
  );
}
