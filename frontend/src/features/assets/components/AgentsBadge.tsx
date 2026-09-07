import { Badge } from '~/components/ui/badge';

const AGENT_LABELS: Record<string, string> = { claude: 'Claude', codex: 'Codex' };

function agentLabel(agent: string): string {
  return AGENT_LABELS[agent] ?? agent;
}

interface AgentsBadgeProps {
  provider: string;
  agents: string[];
}

/**
 * Which coding agents can load this asset. A generic skill (one real copy under
 * ~/.agents/skills) names the agents whose folder links to it; anything in one
 * agent's own folder is that agent's alone.
 */
export function AgentsBadge({ provider, agents }: AgentsBadgeProps) {
  if (provider === 'generic') {
    const linked = agents.map(agentLabel);
    return (
      <Badge
        variant={linked.length > 0 ? 'success' : 'outline'}
        title={
          linked.length > 0
            ? `Generic skill in ~/.agents/skills, linked into ${linked.join(' and ')}`
            : 'Generic skill in ~/.agents/skills, not linked into any agent folder yet'
        }
      >
        Generic{linked.length > 0 ? ` · ${linked.join(', ')}` : ' · unlinked'}
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
