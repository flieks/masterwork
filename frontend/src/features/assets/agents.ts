// The settings leaf, not its index: this module must stay free of components and the API client.
import { agentIdLabel } from '~/features/settings/agents';

/**
 * Who loads an asset, in words. A client-free leaf like `paths.ts`. Since v1.50
 * `agents` is who LOADS it, not who links to it: Codex reads ~/.agents/skills
 * natively, Claude Code only through a link in ~/.claude/skills.
 */

/** "Claude Code and Codex", "Codex", or null for nobody. */
export function agentsPhrase(agents: readonly string[]): string | null {
  const labels = agents.map((agent) => agentIdLabel(agent));
  if (labels.length === 0) return null;
  if (labels.length === 1) return labels[0];
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

/** "Claude Code and Codex load it" / "Codex loads it" / "no agent loads it yet", for a toast. */
export function loadedByPhrase(agents: readonly string[]): string {
  const who = agentsPhrase(agents);
  if (!who) return 'no agent loads it yet';
  return `${who} ${agents.length === 1 ? 'loads' : 'load'} it`;
}
