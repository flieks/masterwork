import { useAtom } from 'jotai';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Bot, CircleSlash, Terminal } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Card } from '~/components/ui/card';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { TrackingBanner, integrationsQueryAtom, isRecording } from '~/features/observability';
import {
  INTERRUPTED_NEVER_DERIVED,
  codingSessionsQueryAtom,
  isSessionLive,
  showAutomatedAtom,
  statusFilterAtom,
} from '../queries';
import { AssetUsagePanel } from './AssetUsagePanel';
import { LiveIndicator } from './LiveIndicator';
import { RunCard } from './RunCard';
import { RunFilters } from './RunFilters';

type View = 'runs' | 'assets';

/**
 * Runs and the assets they used are two readings of the same recording, so they
 * are two tabs rather than two screens. The view lives in the URL so a rollup
 * can be linked to; Radix unmounts the inactive panel, which also stops the run
 * grid polling while the rollup is open.
 */
export function SessionsListPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('view');
  const view: View = raw === 'assets' ? 'assets' : 'runs';

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Sessions</h1>
        <p className="text-sm text-muted-foreground">
          Every pipeline run and coding session, recorded from hook events as they fire — and which
          skills and agents each one used.
        </p>
      </header>

      <TrackingBanner />

      <Tabs
        value={view}
        onValueChange={(next) =>
          setParams(next === 'runs' ? {} : { view: next }, { replace: true })
        }
        className="flex flex-col gap-4"
      >
        <TabsList className="self-start">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="assets">Assets</TabsTrigger>
        </TabsList>

        <TabsContent value="runs" className="flex flex-col gap-4">
          <RunsView />
        </TabsContent>
        <TabsContent value="assets">
          <AssetUsagePanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function RunsView() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(codingSessionsQueryAtom);
  const [{ data: integrations }] = useAtom(integrationsQueryAtom);
  const [status] = useAtom(statusFilterAtom);
  const liveCount = data?.filter((s) => isSessionLive(s)).length ?? 0;

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {data ? (
            <span className="font-mono text-sm text-muted-foreground">
              {data.length} {data.length === 1 ? 'run' : 'runs'}
            </span>
          ) : null}
          {liveCount > 0 ? <LiveIndicator label={`${liveCount} live`} /> : null}
          <RunFilters />
        </div>
        <AutomatedToggle />
      </div>

      {isPending ? (
        <RunGridSkeleton />
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load sessions"
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : data.length === 0 && status === 'interrupted' ? (
        // Not "nothing matched": nothing can match, and saying so is the point.
        <EmptyState
          icon={<CircleSlash className="size-8" />}
          title="No run reports itself interrupted"
          description={INTERRUPTED_NEVER_DERIVED}
        />
      ) : data.length === 0 ? (
        <EmptyState
          icon={<Terminal className="size-8" />}
          title="No runs recorded yet"
          description={
            isRecording(integrations)
              ? 'Start a coding session and it shows up here within a couple of seconds.'
              : 'Connect your coding agent above — from then on every session it runs lands here.'
          }
        />
      ) : (
        <RunGrid sessions={data} />
      )}
    </>
  );
}

function RunGrid({ sessions }: { sessions: CodingSession[] }) {
  // One clock for the whole grid, so every card's axis agrees on where "now" is.
  const now = Date.now();
  // Rendered in the order the server sent: live runs first, then most recent.
  // Never re-sorted here — the grid flows left to right, so top-left is newest.
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {sessions.map((session) => (
        <RunCard key={session.id} session={session} now={now} />
      ))}
    </div>
  );
}

function AutomatedToggle() {
  const [showAutomated, setShowAutomated] = useAtom(showAutomatedAtom);

  return (
    <Button
      variant={showAutomated ? 'secondary' : 'outline'}
      size="sm"
      aria-pressed={showAutomated}
      onClick={() => setShowAutomated(!showAutomated)}
      title="Runs started by a script, hook or scheduler rather than by you"
    >
      <Bot className="size-4" />
      {showAutomated ? 'Hide automated' : 'Show automated'}
    </Button>
  );
}

function RunGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="flex flex-col gap-3 p-4">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-16 w-full" />
          <div className="flex items-center justify-between">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-4 w-24" />
          </div>
        </Card>
      ))}
    </div>
  );
}
