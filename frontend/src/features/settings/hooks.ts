import { useAtom } from 'jotai';
import { assistantAgentFrom, type AssistantAgent } from './agents';
import { appSettingsQueryAtom } from './queries';

/** The agent every AI feature runs on right now, for copy like "Codex is thinking". */
export function useAssistantAgent(): AssistantAgent {
  const [{ data }] = useAtom(appSettingsQueryAtom);
  return assistantAgentFrom(data);
}
