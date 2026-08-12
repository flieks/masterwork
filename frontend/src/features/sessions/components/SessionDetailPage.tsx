import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link, useParams } from 'react-router-dom';
import { AlertTriangle, ChevronRight } from 'lucide-react';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import {
  codingSessionEventsQueryAtom,
  codingSessionQueryAtom,
  isSessionLive,
  runIdLabel,
} from '../queries';
import { ChildRuns } from './ChildRuns';
import { EventTimeline } from './EventTimeline';
import { RouteDecisionNote } from './RouteDecisionNote';
import { PhasePanel } from './PhasePanel';
import { RunWaterfall } from './RunWaterfall';
import { SessionAssets } from './SessionAssets';
import { SessionHeader } from './SessionHeader';
import { UnattributedEvidence } from './UnattributedEvidence';

export function SessionDetailPage() {
  const { id = '' } = useParams();
  const [{ data: session, isPending, isError, error }] = useAtom(codingSessionQueryAtom(id));

  if (isPending) {
    return (
      <div className="mx-auto w-full max-w-6xl space-y-4 p-6">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError || !session) {
    const notFound = apiErrorMessage(error).toLowerCase().includes('not found');
    return (
      <div className="mx-auto w-full max-w-6xl p-6">
        <Breadcrumb runId={id} />
        <EmptyState
          className="mt-4"
          icon={<AlertTriangle className="size-8" />}
          title={notFound ? 'Session not found' : "Couldn't load session"}
          description={notFound ? id : apiErrorMessage(error)}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <Breadcrumb runId={runIdLabel(session)} />
      <SessionHeader session={session} />
      <RouteDecisionNote sessionId={session.id} />
      <SessionAssets session={session} />
      <ChildRuns sessionId={session.id} childCount={session.child_count} />
      <RunViews sessionId={session.id} />
    </div>
  );
}

/** Waterfall by default; the full stream stays one click away. */
function RunViews({ sessionId }: { sessionId: string }) {
  const [{ data: session }] = useAtom(codingSessionQueryAtom(sessionId));
  const [{ data: events }] = useAtom(codingSessionEventsQueryAtom(sessionId));
  const [selectedPhaseId, setSelectedPhaseId] = useState<number | null>(null);

  if (!session) return null;
  const live = isSessionLive(session);
  const selected = session.phases.find((phase) => phase.id === selectedPhaseId) ?? null;

  return (
    <Tabs defaultValue="waterfall" className="flex flex-col gap-3">
      <TabsList className="self-start">
        <TabsTrigger value="waterfall">Waterfall</TabsTrigger>
        <TabsTrigger value="events">
          All events
          <span className="rounded-full bg-muted-foreground/15 px-1.5 text-xs tabular-nums">
            {session.event_count}
          </span>
        </TabsTrigger>
      </TabsList>

      <TabsContent value="waterfall" className="flex flex-col gap-3">
        <RunWaterfall
          session={session}
          events={events ?? []}
          now={Date.now()}
          selectedPhaseId={selectedPhaseId}
          onSelectPhase={(phaseId) =>
            setSelectedPhaseId((current) => (current === phaseId ? null : phaseId))
          }
        />
        {selected ? (
          <PhasePanel session={session} phase={selected} onClose={() => setSelectedPhaseId(null)} />
        ) : (
          <p className="text-xs text-muted-foreground">
            Select a phase to see its gate checks, envelope attempts, events and commit.
          </p>
        )}
        <UnattributedEvidence session={session} />
      </TabsContent>

      <TabsContent value="events">
        <EventTimeline sessionId={sessionId} live={live} />
      </TabsContent>
    </Tabs>
  );
}

function Breadcrumb({ runId }: { runId: string }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-muted-foreground">
      <Link to="/sessions" className="transition-colors hover:text-foreground">
        Sessions
      </Link>
      <ChevronRight className="size-3.5" aria-hidden="true" />
      <span className="font-mono text-foreground">{runId}</span>
    </nav>
  );
}
