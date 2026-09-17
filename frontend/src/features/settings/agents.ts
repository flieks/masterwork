import type { AgentId, AgentInfo, AppSettings } from '~/api/generated';

/** What copy calls the assistant before settings have loaded (or when they failed). */
export const ASSISTANT_FALLBACK_LABEL = 'the assistant';

const KNOWN_LABELS: Record<AgentId, string> = { claude: 'Claude Code', codex: 'Codex' };

export interface AssistantAgent {
  id: AgentId | null;
  label: string;
}

/** Label for an agent id; the settings list wins so the backend owns the wording. */
export function agentIdLabel(id: string, agents?: AgentInfo[] | null): string {
  return agents?.find((agent) => agent.id === id)?.label ?? KNOWN_LABELS[id as AgentId] ?? id;
}

export function assistantAgentFrom(settings: AppSettings | undefined): AssistantAgent {
  const id = settings?.assistant_agent;
  if (!isAgentId(id)) return { id: null, label: ASSISTANT_FALLBACK_LABEL };
  return { id, label: agentIdLabel(id, settings?.agents) };
}

/** "the assistant is thinking" → "The assistant is thinking"; agent names are already capitalised. */
export function atSentenceStart(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function isAgentId(value: unknown): value is AgentId {
  return value === 'claude' || value === 'codex';
}
