import { useEffect, useRef, useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Bell, MessageCircleQuestion } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '~/components/ui/card';
import { absoluteDateTime } from '~/lib/datetime';
import { waitingRunsQueryAtom } from '../queries';
import { runTitleMeta, sessionDetailPath, sessionLabel, waitedFor } from '../runs';

/**
 * The runs blocked on a person right now.
 *
 * Mounted outside the tabs and above the grid, for the same reason the
 * interview form is: this is the one state someone still has to act on, and a
 * question that waits six hours behind a filter is a question nobody answers.
 */
export function WaitingRuns() {
  const [{ data, isPending, isError }] = useAtom(waitingRunsQueryAtom);
  const waiting = data ?? [];
  useWaitingNotifications(waiting);

  if (isPending || isError || waiting.length === 0) return null;

  return (
    <Card
      data-testid="waiting-runs"
      className="border-amber-500/40 bg-amber-500/[0.04] dark:bg-amber-500/[0.06]"
    >
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-amber-700 dark:text-amber-400">
            <MessageCircleQuestion className="size-4" aria-hidden="true" />
            {waiting.length === 1 ? 'A run is waiting on you' : `${waiting.length} runs are waiting`}
          </CardTitle>
          <CardDescription>
            Asked a question or hit a permission prompt mid-turn. Nothing moves until it is
            answered, in the session it came from.
          </CardDescription>
        </div>
        <NotifyToggle />
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {waiting.map((session) => (
          <WaitingRow key={session.id} session={session} />
        ))}
      </CardContent>
    </Card>
  );
}

function WaitingRow({ session }: { session: CodingSession }) {
  const title = runTitleMeta(session);
  const since = session.awaiting_input_since;

  return (
    <Link
      to={sessionDetailPath(session.id)}
      data-testid="waiting-run"
      className="flex items-center gap-3 rounded-md border bg-background px-3 py-2 transition-colors hover:border-ring hover:bg-accent/40"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{title.text}</p>
        <p className="truncate font-mono text-[11px] text-muted-foreground">
          {sessionLabel(session)}
        </p>
      </div>
      {since ? (
        <span
          className="shrink-0 text-xs text-amber-700 dark:text-amber-400"
          title={`Waiting since ${absoluteDateTime(since)}`}
        >
          {waitedFor(since)}
        </span>
      ) : null}
    </Link>
  );
}

/**
 * A desktop notification the first time a run goes quiet on a question.
 *
 * Keyed on the session id, so a run that stays blocked notifies once rather
 * than on every poll — and a run that gets answered and blocks again notifies
 * again, which is the behaviour someone watching would expect. Permission is
 * never asked for on load: an unprompted browser permission dialog is how this
 * ends up denied for good, so the ask is behind the button.
 */
function useWaitingNotifications(waiting: CodingSession[]) {
  const announced = useRef(new Set<string>());

  useEffect(() => {
    const ids = new Set(waiting.map((session) => session.id));
    for (const id of announced.current) {
      if (!ids.has(id)) announced.current.delete(id);
    }
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    for (const session of waiting) {
      if (announced.current.has(session.id)) continue;
      announced.current.add(session.id);
      new Notification('A run is waiting on you', {
        body: runTitleMeta(session).text,
        tag: `masterwork-waiting-${session.id}`,
      });
    }
  }, [waiting]);
}

/** Asks for notification permission, and says nothing once it has an answer. */
function NotifyToggle() {
  const supported = typeof Notification !== 'undefined';
  const [permission, setPermission] = useState(supported ? Notification.permission : 'denied');
  if (!supported || permission !== 'default') return null;

  return (
    <Button
      variant="outline"
      size="sm"
      className="shrink-0"
      onClick={() => void Notification.requestPermission().then(setPermission)}
    >
      <Bell className="size-3.5" />
      Notify me
    </Button>
  );
}
