import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, ScrollText, Sparkles } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { generateSummaryMutationAtom } from '../queries';

/** Summary tab: one generated digest of every applied asset change (chat
 * proposals + simulation suggestions) — a global overview plus a per-asset
 * breakdown. Persisted on the project, so it survives reloads. */
export function ProjectSummaryTab({ project }: { project: Project }) {
  const [{ mutateAsync: generate, isPending }] = useAtom(generateSummaryMutationAtom);
  const queryClient = useQueryClient();

  async function handleGenerate() {
    try {
      await generate(project.id);
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
      toast.success('Summary generated');
    } catch (err) {
      toast.error('Could not generate the summary', { description: apiErrorMessage(err) });
    }
  }

  const generateButton = (
    <Button size="sm" onClick={handleGenerate} disabled={isPending}>
      {isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
      {isPending ? 'Generating…' : project.change_summary ? 'Regenerate' : 'Generate'}
    </Button>
  );

  return (
    <div className="mx-auto w-full max-w-4xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Change summary</h2>
          <p className="text-sm text-muted-foreground">
            What changed across your skills and agents — from accepted chat proposals and applied
            simulation suggestions.
          </p>
        </div>
        {generateButton}
      </div>

      {isPending && (
        <p className="mt-4 text-xs text-muted-foreground">
          Claude is summarizing the change log — this takes up to a minute.
        </p>
      )}

      {project.change_summary ? (
        <div className="mt-6 space-y-3">
          {project.change_summary_at && (
            <p
              className="text-xs text-muted-foreground"
              title={absoluteDateTime(project.change_summary_at)}
            >
              Generated {relativeTime(project.change_summary_at)}
            </p>
          )}
          <MarkdownView content={project.change_summary} />
        </div>
      ) : (
        !isPending && (
          <EmptyState
            className="mt-16"
            icon={<ScrollText className="size-8" />}
            title="No summary yet"
            description="Generate one to see how your toolkit evolved — a global overview plus what changed per skill and agent."
          />
        )
      )}
    </div>
  );
}
