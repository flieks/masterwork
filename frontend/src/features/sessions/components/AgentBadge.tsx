import { cn } from '~/lib/utils';

/** What each `source` is called on screen; an agent this UI has not met keeps its id. */
const AGENT_LABELS: Record<string, string> = {
  'claude-code': 'Claude Code',
  codex: 'Codex',
};

function agentLabel(source: string): string {
  return AGENT_LABELS[source] ?? source;
}

/**
 * Which coding agent recorded the run. Small on purpose: with two agents wired
 * up the grid mixes their runs, and this is the one glance that tells them apart.
 */
export function AgentBadge({ source, className }: { source: string; className?: string }) {
  return (
    <span
      data-agent={source}
      title={`Recorded by ${agentLabel(source)}`}
      className={cn(
        'inline-flex shrink-0 items-center rounded-md border border-border/60 bg-muted/60 px-1.5 py-px font-mono text-[10px] font-medium text-muted-foreground',
        className,
      )}
    >
      {agentLabel(source)}
    </span>
  );
}
