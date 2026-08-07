import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Sparkles, RefreshCw, AlertTriangle } from 'lucide-react';
import type { AssetDiagram } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { MermaidView } from '~/components/MermaidView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { relativeTime } from '~/lib/datetime';
import {
  assetDiagramQueryAtom,
  generateAssetDiagramMutationAtom,
  type AssetKind,
} from '../queries';

export function AssetDiagramSection({ assetId, kind }: { assetId: string; kind: AssetKind }) {
  const [{ data: diagram, isPending }] = useAtom(assetDiagramQueryAtom(assetId));
  const [{ mutateAsync: generate, isPending: isGenerating }] = useAtom(
    generateAssetDiagramMutationAtom,
  );
  const queryClient = useQueryClient();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!isGenerating) {
      setElapsed(0);
      return;
    }
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [isGenerating]);

  async function onGenerate() {
    try {
      const result = await generate(assetId);
      queryClient.setQueryData(['assetDiagram', assetId], result);
      toast.success('Diagram generated');
    } catch (err) {
      toast.error('Generation failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <DiagramPanel
      diagram={diagram ?? null}
      noun={kind}
      isPending={isPending}
      isGenerating={isGenerating}
      elapsedSeconds={elapsed}
      onGenerate={onGenerate}
    />
  );
}

interface DiagramPanelProps {
  diagram: AssetDiagram | null;
  noun: string;
  isPending: boolean;
  isGenerating: boolean;
  elapsedSeconds: number;
  onGenerate: () => void;
}

/** Collapsible diagram panel (collapsed by default) — the three data states (none/fresh/stale). */
export function DiagramPanel({
  diagram,
  noun,
  isPending,
  isGenerating,
  elapsedSeconds,
  onGenerate,
}: DiagramPanelProps) {
  return (
    <details className="rounded-md border bg-muted/30">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">
        Diagram
        {isGenerating ? (
          <span className="ml-1.5 font-normal">· generating…</span>
        ) : diagram?.stale ? (
          <span className="ml-1.5 font-normal text-amber-700 dark:text-amber-400">· stale</span>
        ) : !isPending && !diagram ? (
          <span className="ml-1.5 font-normal">· none yet</span>
        ) : null}
      </summary>

      <div className="border-t p-3">
        {isGenerating ? (
          <GeneratingIndicator elapsedSeconds={elapsedSeconds} />
        ) : isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : diagram ? (
          <div className="space-y-2">
            {diagram.stale ? (
              <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
                <AlertTriangle className="size-4 shrink-0" />
                The file changed since this diagram was generated.
              </div>
            ) : null}
            <div className="rounded-md border bg-background p-3">
              <MermaidView source={diagram.mermaid} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                Generated {relativeTime(diagram.generated_at)}
              </p>
              <Button size="sm" variant="outline" onClick={onGenerate}>
                <RefreshCw /> Regenerate
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-2 rounded-md border border-dashed bg-background p-4">
            <p className="text-sm text-muted-foreground">
              No diagram yet. Generate a Mermaid flowchart of how this {noun} works internally.
            </p>
            <Button size="sm" onClick={onGenerate}>
              <Sparkles /> Generate diagram
            </Button>
          </div>
        )}
      </div>
    </details>
  );
}

/** Busy indicator for the slow (up to 300 s) generation call. */
function GeneratingIndicator({ elapsedSeconds }: { elapsedSeconds: number }) {
  return (
    <div
      className="flex items-center gap-2 rounded-md border p-4 text-sm text-muted-foreground"
      aria-live="polite"
    >
      <span className="inline-flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 animate-bounce rounded-full bg-current"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </span>
      <span>
        Generating diagram{elapsedSeconds > 0 ? ` · ${elapsedSeconds}s` : '…'} (this can take a
        minute)
      </span>
    </div>
  );
}
