import { Badge } from '~/components/ui/badge';
import { agentsPhrase } from '../agents';

const AGENT_LABELS: Record<string, string> = { claude: 'Claude', codex: 'Codex' };

function agentLabel(agent: string): string {
  return AGENT_LABELS[agent] ?? agent;
}

interface AgentsBadgeProps {
  provider: string;
  agents: string[];
  /** Parked under .disabled/: the empty `agents` is by design, not a missing link. */
  disabled?: boolean;
  /** "codex-config" when ~/.codex/config.toml switched it off rather than a .disabled/ move. */
  disabledBy?: string | null;
}

/**
 * Which coding agents can load this asset. A generic skill (one real copy under
 * ~/.agents/skills) names the agents whose folder links to it; anything in one
 * agent's own folder is that agent's alone.
 */
export function AgentsBadge({ provider, agents, disabled = false, disabledBy }: AgentsBadgeProps) {
  if (disabled) {
    return (
      <Badge
        variant="outline"
        className="text-muted-foreground"
        title={
          disabledBy === 'codex-config'
            ? 'Switched off in ~/.codex/config.toml, so Codex skips it'
            : 'Switched off: coding agents only read one level deep, and this skill sits under .disabled/'
        }
      >
        Not loaded by any agent
      </Badge>
    );
  }
  if (provider === 'generic') {
    // `agents` is who loads it: Codex reads ~/.agents/skills itself, Claude Code only via a link.
    const loaders = agents.map(agentLabel);
    const who = agentsPhrase(agents);
    return (
      <Badge
        variant={loaders.length > 0 ? 'success' : 'outline'}
        title={
          who
            ? `Generic skill in ~/.agents/skills, loaded by ${who}` +
              (agents.includes('claude') ? '' : '; Claude Code needs a link in ~/.claude/skills')
            : 'Generic skill in ~/.agents/skills that no agent loads: Claude Code needs a link in ~/.claude/skills, and ~/.codex/config.toml switches it off for Codex'
        }
      >
        Generic · {loaders.length > 0 ? loaders.join(', ') : 'no agent'}
      </Badge>
    );
  }
  if (provider === 'masterwork') {
    return (
      <Badge variant="muted" title="A factory role, read by masterwork's own pipeline">
        Factory
      </Badge>
    );
  }
  const only = agents.map(agentLabel);
  const label = only.length > 0 ? `${only.join(', ')} only` : 'No agent';
  return (
    <Badge
      variant="secondary"
      title={
        only.length > 0
          ? `Lives in ${only.join(', ')}'s own folder; other coding agents don't see it`
          : undefined
      }
    >
      {label}
    </Badge>
  );
}
