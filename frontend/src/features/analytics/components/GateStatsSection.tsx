import { useAtom } from 'jotai';
import { ShieldCheck } from 'lucide-react';
import type { GateStat } from '~/api/generated';
import { Card } from '~/components/ui/card';
import { EmptyState } from '~/components/EmptyState';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { gateStatsQueryAtom } from '../queries';
import { denominatorLabel, failingGates, roleLabel, UNATTRIBUTED_ROLE_HINT } from '../stats';
import { Rate, RateBar } from './Rate';
import { SectionShell } from './SectionShell';

/**
 * Which gate keeps failing, and in whose hands.
 *
 * The lead view, because it is the one that answers the question the screen
 * exists for: a gate's failure notes name the instruction that needs fixing,
 * and its `by_role` split says whose instruction it is.
 */
export function GateStatsSection() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(gateStatsQueryAtom);
  const failing = data ? failingGates(data) : [];

  return (
    <SectionShell
      title="Gates"
      description="Every deterministic check the pipeline ran, ranked by how often it failed. The notes are verbatim — a gate names the files or fields it is about, so two notes are only ever grouped when they are the same sentence."
      count={
        data ? `${denominatorLabel(data.length, 'gate')} · ${failing.length} failing` : undefined
      }
      isPending={isPending}
      isError={isError}
      error={error}
      onRetry={refetch}
      empty={
        data?.length === 0 ? (
          <EmptyState
            icon={<ShieldCheck className="size-8" />}
            title="No gate check recorded in this population"
            description="Gate names and notes come from the per-check evidence rows, which only pipeline runs write. Widen the window, switch the workflow filter to Factory, or backfill a run recorded before the evidence pass to populate this."
          />
        ) : null
      }
    >
      <div className="flex flex-col gap-3">
        {data?.map((gate) => (
          <GateCard key={gate.gate} gate={gate} />
        ))}
      </div>
    </SectionShell>
  );
}

function GateCard({ gate }: { gate: GateStat }) {
  const clean = gate.failures === 0;

  return (
    // Named after the gate so the card is addressable as one thing — by a
    // screen reader walking the section, and by a test scoping to it.
    <Card
      role="group"
      aria-label={gate.gate}
      className={cn('flex min-w-0 flex-col gap-3 p-4', clean && 'bg-muted/20')}
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex items-baseline gap-2">
          <h3 className="font-mono text-sm font-semibold">{gate.gate}</h3>
          <span className="text-xs text-muted-foreground">
            {denominatorLabel(gate.runs, 'run')}
          </span>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Failure rate
          </span>
          <Rate rate={gate.failure_rate} count={gate.checks} noun="check" />
          <span className="text-xs text-muted-foreground">
            {denominatorLabel(gate.failures, 'failure')}
          </span>
        </div>
      </div>

      {/*
        A gate that never failed still has to be visible — its checks are the
        evidence that it ran — but it is not what the reader came for, so its
        role split folds away rather than pushing the failing gates off screen.
        Nothing is dropped: the disclosure holds the same table.
      */}
      {clean ? (
        <details className="group">
          <summary className="cursor-pointer text-xs text-muted-foreground marker:text-muted-foreground">
            Every check passed, across {denominatorLabel(gate.by_role.length, 'role')} — show the
            split
          </summary>
          <div className="pt-2">
            <RoleBreakdown roles={gate.by_role} />
          </div>
        </details>
      ) : (
        <RoleBreakdown roles={gate.by_role} />
      )}

      {gate.top_failure_notes.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            What it said, verbatim
          </p>
          {gate.top_failure_notes.map((note) => (
            <div
              key={`${note.role}:${note.note}:${note.last_seen_at}`}
              className="rounded-md border border-destructive/30 bg-destructive/5 p-2.5"
            >
              {/*
                Never truncated: the note is the diagnostic payload, and a gate
                that says which three fields were missing is only useful if you
                can read all three.
              */}
              <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                {note.note}
              </p>
              <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                <span
                  className="rounded border px-1 font-medium"
                  title={note.role === null ? UNATTRIBUTED_ROLE_HINT : undefined}
                >
                  {roleLabel(note.role)}
                </span>
                {note.occurrences > 1 ? (
                  <span>{denominatorLabel(note.occurrences, 'time')}</span>
                ) : (
                  <span>once</span>
                )}
                <time dateTime={note.last_seen_at} title={absoluteDateTime(note.last_seen_at)}>
                  last {relativeTime(note.last_seen_at)}
                </time>
              </p>
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

/** The same gate as each role experienced it: whose stage keeps tripping it. */
function RoleBreakdown({ roles }: { roles: GateStat['by_role'] }) {
  if (roles.length === 0) {
    return <p className="text-xs text-muted-foreground">No role reported a stage for this gate.</p>;
  }

  // `relative`: the sr-only caption is absolutely positioned, and without a
  // positioned ancestor inside the scroll box it widens the page instead.
  return (
    <div className="relative min-w-0 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Failure rate by role</caption>
        <thead>
          <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th scope="col" className="py-1 pr-3 font-medium">
              Role
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              Checks
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              Failures
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Rate
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {roles.map((row) => (
            <tr
              key={roleLabel(row.role)}
              className={cn(row.failures === 0 && 'text-muted-foreground')}
            >
              <td className="py-1.5 pr-3">
                <span
                  className="font-mono text-xs"
                  title={row.role === null ? UNATTRIBUTED_ROLE_HINT : undefined}
                >
                  {roleLabel(row.role)}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-right font-mono text-xs tabular-nums">
                {row.checks}
              </td>
              <td
                className={cn(
                  'py-1.5 pr-3 text-right font-mono text-xs tabular-nums',
                  row.failures > 0 && 'font-semibold text-destructive',
                )}
              >
                {row.failures}
              </td>
              <td className="py-1.5 text-right">
                <RateBar rate={row.failure_rate} count={row.checks} noun="check" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
