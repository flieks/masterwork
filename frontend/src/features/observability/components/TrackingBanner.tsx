import { useState } from 'react';
import { useAtom } from 'jotai';
import { Activity, Ban, Loader2, Plug, RefreshCw } from 'lucide-react';
import type { ObservabilityIntegration } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { toast } from '~/components/ui/sonner';
import {
  connectIntegrationMutationAtom,
  disconnectIntegrationMutationAtom,
  integrationsQueryAtom,
} from '../queries';

/**
 * Setup for session recording, on the screen that needs it.
 *
 * Nothing is installed behind the user's back — this tool edits files in the
 * home directory, so wiring an agent up stays an explicit click, and every
 * click is reversible from the same place.
 *
 * Silent while nothing is known yet: a card that appears and vanishes on every
 * page load reads as a defect.
 */
export function TrackingBanner() {
  const [{ data }] = useAtom(integrationsQueryAtom);
  const [open, setOpen] = useState(false);

  if (!data || data.length === 0) return null;
  const recording = data.filter((i) => i.state === 'connected');

  if (recording.length === 0) return <SetupCard integrations={data} />;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {/* Steady, not the pulsing live dot: this is a standing setting, not an
            event happening right now. */}
        <span className="inline-flex items-center gap-1.5 font-medium text-emerald-600 dark:text-emerald-400">
          <span className="size-2 rounded-full bg-emerald-500" />
          Recording {recording.map((i) => i.label).join(', ')}
        </span>
        <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Manage'}
        </Button>
      </div>
      {open ? (
        <Card>
          <CardContent className="flex flex-col gap-4 pt-6">
            {data.map((integration) => (
              <IntegrationRow key={integration.id} integration={integration} />
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function SetupCard({ integrations }: { integrations: ObservabilityIntegration[] }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="size-4" />
          Record your coding sessions
        </CardTitle>
        <CardDescription>
          Masterwork can only show a run once your coding agent tells it one happened. Connecting
          writes the hooks that do the telling — into your agent&apos;s own config, backed up first,
          and removable from right here.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {integrations.map((integration) => (
          <IntegrationRow key={integration.id} integration={integration} />
        ))}
      </CardContent>
    </Card>
  );
}

function IntegrationRow({ integration }: { integration: ObservabilityIntegration }) {
  const [{ mutateAsync: connect, isPending: connecting }] = useAtom(connectIntegrationMutationAtom);
  const [{ mutateAsync: disconnect, isPending: disconnecting }] = useAtom(
    disconnectIntegrationMutationAtom,
  );
  const busy = connecting || disconnecting;
  const connected = integration.state === 'connected';

  async function run(action: (id: string) => Promise<ObservabilityIntegration>, done: string) {
    try {
      await action(integration.id);
      toast(done);
    } catch (err) {
      toast.error(apiErrorMessage(err));
    }
  }

  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium">{integration.label}</p>
        <p className="text-sm text-muted-foreground">{integration.detail}</p>
        {connected ? (
          <p className="truncate font-mono text-[11px] text-muted-foreground">
            {integration.config_path}
          </p>
        ) : null}
      </div>

      {connected ? (
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => run(disconnect, `${integration.label} is no longer recording.`)}
        >
          {disconnecting ? <Loader2 className="size-4 animate-spin" /> : <Ban className="size-4" />}
          Disconnect
        </Button>
      ) : (
        <Button
          size="sm"
          disabled={busy || integration.state === 'unavailable'}
          onClick={() =>
            run(connect, `${integration.label} is recording. New sessions appear here.`)
          }
        >
          {connecting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : integration.state === 'outdated' ? (
            <RefreshCw className="size-4" />
          ) : (
            <Plug className="size-4" />
          )}
          {integration.state === 'outdated' ? 'Repair' : `Connect ${integration.label}`}
        </Button>
      )}
    </div>
  );
}
