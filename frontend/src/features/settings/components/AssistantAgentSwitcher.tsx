import { useAtom } from 'jotai';
import { AlertTriangle } from 'lucide-react';
import type { AgentId } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { agentIdLabel } from '../agents';
import { appSettingsQueryAtom, updateSettingsMutationAtom } from '../queries';

/** Picks which CLI (Claude Code or Codex) every AI feature in masterwork runs on. */
export function AssistantAgentSwitcher() {
  const [{ data: settings, isPending, isError, refetch }] = useAtom(appSettingsQueryAtom);
  const [{ mutateAsync: save, isPending: isSaving }] = useAtom(updateSettingsMutationAtom);

  async function pick(next: AgentId) {
    if (!settings || next === settings.assistant_agent) return;
    const label = agentIdLabel(next, settings.agents);
    try {
      await save({ assistant_agent: next });
      toast.success(`Assistant switched to ${label}`, {
        description: 'Chat, simulations and every other AI feature now run on it.',
      });
    } catch (err) {
      toast.error(`Could not switch to ${label}`, { description: apiErrorMessage(err) });
    }
  }

  const current = settings?.agents?.find((agent) => agent.id === settings.assistant_agent);

  return (
    <div className="space-y-1">
      <label htmlFor="assistant-agent" className="text-[11px] font-medium text-muted-foreground">
        Assistant
      </label>
      {isPending ? (
        <Skeleton className="h-8 w-full" />
      ) : isError || !settings ? (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <AlertTriangle className="size-3.5 shrink-0" /> Couldn't load
          <Button size="sm" variant="ghost" className="h-6 px-2" onClick={() => void refetch()}>
            Retry
          </Button>
        </p>
      ) : (
        <select
          id="assistant-agent"
          value={settings.assistant_agent}
          onChange={(e) => void pick(e.target.value as AgentId)}
          disabled={isSaving}
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        >
          {(settings.agents ?? []).map((agent) => (
            <option key={agent.id} value={agent.id} disabled={!agent.installed}>
              {agent.installed ? agent.label : `${agent.label} (not installed)`}
            </option>
          ))}
        </select>
      )}
      {current && !current.installed ? (
        // The backend keeps an uninstalled pick rather than silently switching, so say so.
        <p className="text-[11px] leading-snug text-amber-700 dark:text-amber-400">
          {current.label} isn't installed — AI features fail until it is.
        </p>
      ) : null}
    </div>
  );
}
