import { useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useAtom } from 'jotai';
import { ChevronDown, ChevronRight, MessageSquare } from 'lucide-react';
import { cn } from '~/lib/utils';
import { codingSessionEventsQueryAtom } from '../queries';
import { firstRequest } from '../events';
import { eventImages } from '../media';
import { EventImages } from './EventImages';

/**
 * True while this element is showing less than it holds.
 *
 * The clamp is three *rendered* lines, not three newlines — a prompt is as
 * often one long paragraph as it is a list — so how much is hidden is a
 * question only the browser can answer, and it answers it again at every width.
 */
function useClamped(
  text: string,
  active: boolean,
): [RefObject<HTMLParagraphElement>, boolean] {
  const ref = useRef<HTMLParagraphElement>(null);
  const [clamped, setClamped] = useState(false);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !active) return;
    const measure = () => setClamped(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [text, active]);

  return [ref, clamped];
}

/**
 * What the run was actually asked, under the title that summarises it.
 *
 * The card and the header now show a phrase the agent wrote, not the prompt —
 * so the prompt has to be somewhere, and this is it: three lines by default,
 * because a request is recognised by its opening and read in full only when
 * the summary looks wrong.
 */
export function SessionRequest({ sessionId }: { sessionId: string }) {
  const [{ data: events }] = useAtom(codingSessionEventsQueryAtom(sessionId));
  const [expanded, setExpanded] = useState(false);
  const request = firstRequest(events ?? []);
  const [ref, clamped] = useClamped(request?.text ?? '', !expanded);
  if (!request) return null;

  return (
    <section aria-labelledby="session-request" className="rounded-lg border bg-muted/30 p-3">
      <h2
        id="session-request"
        className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      >
        <MessageSquare className="size-3.5" aria-hidden="true" />
        Request
      </h2>

      <p
        ref={ref}
        className={cn(
          'mt-1.5 whitespace-pre-wrap break-words text-sm leading-relaxed',
          !expanded && 'line-clamp-3',
        )}
      >
        {request.text}
      </p>

      <EventImages sessionId={sessionId} images={eventImages(request.event)} />

      {clamped ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-2 flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {expanded ? 'Show less' : 'Show full request'}
        </button>
      ) : null}
    </section>
  );
}
