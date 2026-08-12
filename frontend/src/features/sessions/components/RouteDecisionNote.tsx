import { useAtom } from 'jotai';
import { Route } from 'lucide-react';
import { codingSessionEventsQueryAtom } from '../queries';
import { routeDecision } from '../runs';

/**
 * The factory-or-chat router's verdict, when this session recorded one — the
 * one-line answer to "why did this task stay in chat / go to the factory".
 */
export function RouteDecisionNote({ sessionId }: { sessionId: string }) {
  const [{ data: events }] = useAtom(codingSessionEventsQueryAtom(sessionId));
  const decision = routeDecision(events ?? []);
  if (!decision) return null;

  return (
    <p className="flex items-center gap-1.5 rounded-lg border bg-muted/30 px-2.5 py-1.5 text-xs">
      <Route className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="font-medium">Routed to {decision.verdict}</span>
      {decision.reason ? (
        <span className="truncate text-muted-foreground" title={decision.reason}>
          — {decision.reason}
        </span>
      ) : null}
    </p>
  );
}
