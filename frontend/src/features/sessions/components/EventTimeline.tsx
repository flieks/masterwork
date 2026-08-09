import { useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, ChevronRight, Radio } from 'lucide-react';
import type { CodingEvent } from '~/api/generated';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, clockTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import {
  groupEvents,
  promptText,
  showsEventType,
  toolSummary,
  type TimelineRow,
  type ToolSummary,
} from '../events';
import { codingSessionEventsQueryAtom } from '../queries';
import { EventTypeChip } from './EventTypeChip';
import { ToolChip } from './ToolChip';

/** A single hook payload can be up to 32k chars — cap what we put in the DOM. */
const MAX_PAYLOAD_CHARS = 4000;

interface EventTimelineProps {
  sessionId: string;
  /** Drives the "waiting for events" copy; the poll itself is cache-driven. */
  live?: boolean;
  /** Narrow the same cached stream to one phase — no extra request. */
  phaseId?: number;
}

export function EventTimeline({ sessionId, live = false, phaseId }: EventTimelineProps) {
  const [{ data: all, isPending, isError, error }] = useAtom(
    codingSessionEventsQueryAtom(sessionId),
  );
  const data =
    all && phaseId !== undefined ? all.filter((event) => event.phase_id === phaseId) : all;

  if (isPending || !data) {
    return (
      <div className="space-y-3" aria-label="Loading events">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        icon={<AlertTriangle className="size-8" />}
        title="Couldn't load events"
        description={apiErrorMessage(error)}
      />
    );
  }

  if (data.length === 0) {
    return (
      <EmptyState
        icon={<Radio className="size-8" />}
        title="No events yet"
        description={
          phaseId !== undefined
            ? 'This phase recorded no events of its own.'
            : live
              ? 'This session is open but has not fired a hook yet. New events appear here as they arrive.'
              : 'This session recorded no events.'
        }
      />
    );
  }

  const rows = groupEvents(data);

  return (
    <ol className="flex flex-col">
      {rows.map((row, index) =>
        row.kind === 'event' ? (
          <EventRow key={row.key} event={row.event} isLast={index === rows.length - 1} />
        ) : (
          <EventGroupRow key={row.key} row={row} isLast={index === rows.length - 1} />
        ),
      )}
    </ol>
  );
}

/**
 * A run of one tool, as one row: `Read ×4`. Expanding it lays the calls out as
 * the rows they would have been — which four files, each with its own payload.
 */
function EventGroupRow({
  row,
  isLast,
}: {
  row: Extract<TimelineRow, { kind: 'group' }>;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [first] = row.events;

  return (
    <li className="flex gap-3">
      <RowGutter at={first.created_at} isLast={isLast && !expanded} />

      <div className={cn('min-w-0 flex-1', isLast && !expanded ? 'pb-1' : 'pb-4')}>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${row.toolName} ×${row.events.length}`}
          className="flex w-full items-center gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {showsEventType(first) ? <EventTypeChip eventType={row.eventType} /> : null}
          <ToolChip toolName={row.toolName} className="shrink-0" />
          <span className="shrink-0 font-mono text-xs text-muted-foreground">
            ×{row.events.length}
          </span>
          <ChevronRight
            aria-hidden="true"
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground transition-transform',
              expanded && 'rotate-90',
            )}
          />
        </button>

        {expanded ? (
          <ol className="mt-2 flex flex-col border-l pl-3">
            {row.events.map((event, i) => (
              <EventRow key={event.id} event={event} isLast={i === row.events.length - 1} nested />
            ))}
          </ol>
        ) : null}
      </div>
    </li>
  );
}

/** The clock and the thread running down the left of every row. */
function RowGutter({ at, isLast }: { at: string; isLast: boolean }) {
  return (
    <>
      <time
        className="w-12 shrink-0 pt-1.5 text-right font-mono text-xs text-muted-foreground"
        dateTime={at}
        title={absoluteDateTime(at)}
      >
        {clockTime(at)}
      </time>
      <div className="flex flex-col items-center" aria-hidden="true">
        <span className="mt-2 size-2 shrink-0 rounded-full bg-border" />
        {isLast ? null : <span className="w-px flex-1 bg-border" />}
      </div>
    </>
  );
}

function EventRow({
  event,
  isLast,
  nested = false,
}: {
  event: CodingEvent;
  isLast: boolean;
  /** Inside an expanded group: the clock is already carried by the group's row. */
  nested?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const prompt = promptText(event);
  const payload = formatPayload(event.payload);
  const summary = toolSummary(event);

  return (
    <li className="flex gap-3">
      {nested ? null : <RowGutter at={event.created_at} isLast={isLast} />}

      <div className={cn('min-w-0 flex-1', isLast ? 'pb-1' : nested ? 'pb-2' : 'pb-4')}>
        {payload ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={`${event.event_type}${summary ? ` — ${summary.text}` : ''}`}
            className="flex w-full items-center gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <EventHeader event={event} summary={summary} />
            <ChevronRight
              aria-hidden="true"
              className={cn(
                'size-3.5 shrink-0 text-muted-foreground transition-transform',
                expanded && 'rotate-90',
              )}
            />
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <EventHeader event={event} summary={summary} />
          </div>
        )}

        {prompt ? (
          <p
            className={cn(
              'mt-1.5 line-clamp-4 whitespace-pre-wrap break-words text-sm',
              // A machine re-entering the session is not the user speaking, and
              // should not be read in the same voice.
              prompt.automated && 'font-mono text-xs italic text-muted-foreground',
            )}
          >
            {prompt.text}
          </p>
        ) : null}

        {payload && expanded ? (
          <pre className="mt-1.5 max-h-80 overflow-auto rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
            {payload}
          </pre>
        ) : null}
      </div>
    </li>
  );
}

/** Hook name, tool, and what the tool was called with — the row's whole label. */
function EventHeader({ event, summary }: { event: CodingEvent; summary: ToolSummary | null }) {
  return (
    <>
      {showsEventType(event) ? <EventTypeChip eventType={event.event_type} /> : null}
      {event.tool_name ? <ToolChip toolName={event.tool_name} className="shrink-0" /> : null}
      {summary ? (
        <span
          className="min-w-0 truncate font-mono text-xs text-muted-foreground"
          title={summary.title}
        >
          {summary.text}
        </span>
      ) : null}
    </>
  );
}

/** Pretty-printed payload, truncated for display; null when there is nothing to show. */
function formatPayload(payload: Record<string, unknown> | null): string | null {
  if (!payload || Object.keys(payload).length === 0) return null;
  let text: string;
  try {
    text = JSON.stringify(payload, null, 2);
  } catch {
    return null;
  }
  if (text.length <= MAX_PAYLOAD_CHARS) return text;
  return `${text.slice(0, MAX_PAYLOAD_CHARS)}\n… truncated (${text.length.toLocaleString()} characters)`;
}
