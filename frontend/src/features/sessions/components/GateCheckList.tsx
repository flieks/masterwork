import { useState } from 'react';
import { ChevronRight, ShieldCheck, ShieldX } from 'lucide-react';
import type { GateCheckItem } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { cn } from '~/lib/utils';
import { checkLabel, groupChecksByAttempt, isRecovered } from '../evidence';

/**
 * Every check a gate ran, with its note verbatim — the note is the reason the
 * row exists. Failures are laid out flat and unhidden; passes are true but
 * uninteresting one at a time, so they fold into a count you can open.
 */
export function GateCheckList({ checks }: { checks: GateCheckItem[] }) {
  const groups = groupChecksByAttempt(checks);

  return (
    <ol className="flex flex-col gap-3">
      {groups.map((group) => (
        <li key={group.attempt} className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
              Attempt {group.attempt}
            </span>
            {group.failed.length > 0 ? (
              <Badge className="gap-1 border-transparent bg-red-500/15 text-red-700 dark:text-red-400">
                <ShieldX className="size-3" />
                {group.failed.length} failed
              </Badge>
            ) : (
              <Badge variant="success" className="gap-1">
                <ShieldCheck className="size-3" />
                all passed
              </Badge>
            )}
          </div>

          {group.failed.map((check) => (
            <FailedCheck key={check.id} check={check} />
          ))}

          {group.passed.length > 0 ? <PassedChecks checks={group.passed} /> : null}
        </li>
      ))}
    </ol>
  );
}

/** The diagnostic payload: never collapsed, never truncated, never paraphrased. */
function FailedCheck({ check }: { check: GateCheckItem }) {
  return (
    <div className="rounded-md border border-red-500/40 bg-red-500/5 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldX className="size-3.5 shrink-0 text-red-600 dark:text-red-400" aria-hidden="true" />
        <span className="font-mono text-xs font-semibold text-red-700 dark:text-red-400">
          {checkLabel(check)}
        </span>
        <OriginNote check={check} />
      </div>
      {check.note ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-sm">{check.note}</p>
      ) : (
        <p className="mt-1 text-sm italic text-muted-foreground">Failed without writing a note.</p>
      )}
    </div>
  );
}

/**
 * "Which gates never fail" is real information, so the passes are reachable —
 * just not at the same volume as the thing that went wrong.
 */
function PassedChecks({ checks }: { checks: GateCheckItem[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex items-center gap-1.5 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn('size-3.5 transition-transform', expanded && 'rotate-90')}
        />
        {checks.length} passed
      </button>

      {expanded ? (
        <ul className="mt-1.5 flex flex-col gap-1 border-l pl-3">
          {checks.map((check) => (
            <li key={check.id} className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <ShieldCheck
                className="size-3 shrink-0 self-center text-emerald-600 dark:text-emerald-400"
                aria-hidden="true"
              />
              <span className="font-mono font-medium">{checkLabel(check)}</span>
              {check.note ? (
                <span className="min-w-0 break-words text-muted-foreground">{check.note}</span>
              ) : null}
              <OriginNote check={check} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** How much to trust the row — only worth saying when it wasn't reported. */
function OriginNote({ check }: { check: GateCheckItem }) {
  if (!isRecovered(check)) return null;
  return (
    <Badge
      variant="muted"
      className="px-1 py-0 text-[10px]"
      title="Rebuilt from the stored event stream rather than reported by the producer."
    >
      recovered
    </Badge>
  );
}
