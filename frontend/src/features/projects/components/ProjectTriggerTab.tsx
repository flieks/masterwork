import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Sparkles, Zap } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { generateTriggerMutationAtom } from '../queries';

/** Trigger tab: a generated guide on how to phrase Claude Code prompts so this
 * project's toolkit fires — entry asset, ready-to-paste prompts, the real
 * trigger phrases per asset, and how the chain runs. Persisted on the project. */
export function ProjectTriggerTab({ project }: { project: Project }) {
  const [{ mutateAsync: generate, isPending }] = useAtom(generateTriggerMutationAtom);
  const queryClient = useQueryClient();

  async function handleGenerate() {
    try {
      await generate(project.id);
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
      toast.success('Trigger guide generated');
    } catch (err) {
      toast.error('Could not generate the guide', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Trigger guide</h2>
          <p className="text-sm text-muted-foreground">
            How to phrase a Claude Code prompt so this toolkit fires — entry point, example
            prompts, and each asset's real trigger phrases.
          </p>
        </div>
        <Button size="sm" onClick={handleGenerate} disabled={isPending}>
          {isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {isPending ? 'Generating…' : project.trigger_guide ? 'Regenerate' : 'Generate'}
        </Button>
      </div>

      {isPending && (
        <p className="mt-4 text-xs text-muted-foreground">
          Claude is reading every linked asset file — this takes a few minutes.
        </p>
      )}

      {project.trigger_guide ? (
        <div className="mt-6 space-y-3">
          {project.trigger_guide_at && (
            <p
              className="text-xs text-muted-foreground"
              title={absoluteDateTime(project.trigger_guide_at)}
            >
              Generated {relativeTime(project.trigger_guide_at)}
            </p>
          )}
          <MarkdownView content={project.trigger_guide} />
        </div>
      ) : (
        !isPending && (
          <EmptyState
            className="mt-16"
            icon={<Zap className="size-8" />}
            title="No trigger guide yet"
            description="Generate one to learn which prompts fire this toolkit and how the skills and agents chain together."
          />
        )
      )}
    </div>
  );
}
