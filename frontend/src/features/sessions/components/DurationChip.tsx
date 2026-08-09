import { Clock3 } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { runDuration } from '~/lib/timeline';
import { cn } from '~/lib/utils';

/**
 * How long the run took, led by the time it was actually working. The wall
 * clock follows in muted text only when it disagrees — an overnight pause makes
 * a 24-second run report 34 hours, and that number is never the answer to
 * "how long did this take?".
 */
export function DurationChip({
  session,
  className,
}: {
  session: Pick<CodingSession, 'active_ms' | 'wall_ms'>;
  className?: string;
}) {
  const { active, elapsed, idle, label } = runDuration(session.active_ms, session.wall_ms);

  return (
    <span
      title={label}
      className={cn(
        // `relative`: the sr-only label is absolutely positioned and would
        // otherwise anchor to an ancestor and widen the page.
        'relative inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground',
        className,
      )}
    >
      <Clock3 className="size-3 shrink-0" aria-hidden="true" />
      <span className="sr-only">Duration: </span>
      {/* One text node, not two flex children: a flex `gap` is not a space, and
          the reading of this chip depends on the words staying joined. */}
      <span className="whitespace-nowrap">
        <span className="text-foreground">{active}</span> active
      </span>
      {idle ? <span className="whitespace-nowrap opacity-60">· {elapsed} elapsed</span> : null}
    </span>
  );
}
