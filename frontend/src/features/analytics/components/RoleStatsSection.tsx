import { useAtom } from 'jotai';
import { Users } from 'lucide-react';
import type { RoleStat } from '~/api/generated';
import { EmptyState } from '~/components/EmptyState';
import { cn } from '~/lib/utils';
import { roleStatsQueryAtom } from '../queries';
import {
  denominatorLabel,
  formatAverage,
  formatMs,
  formatUsd,
  roleLabel,
  UNATTRIBUTED_ROLE_HINT,
} from '../stats';
import { Average, Rate } from './Rate';
import { SectionShell } from './SectionShell';

/**
 * Which role keeps being sent back, and what it costs when it is.
 *
 * Rendered in exactly the order the API sent: corrections descending, then gate
 * failures, then name — worst first. Nothing re-sorts it and no column header
 * offers to, because the ranking *is* the answer; letting the reader sort by
 * cheapest would bury the row the screen exists to surface.
 */
export function RoleStatsSection() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(roleStatsQueryAtom);

  return (
    <SectionShell
      title="Roles"
      description="Every lane across every run, worst first. Gate figures here come from the stage counters, which every run ever recorded carries — so they cover more history than the gate table above and the two can legitimately disagree."
      count={data ? denominatorLabel(data.length, 'role') : undefined}
      isPending={isPending}
      isError={isError}
      error={error}
      onRetry={refetch}
      empty={
        data?.length === 0 ? (
          <EmptyState
            icon={<Users className="size-8" />}
            title="No stage recorded in this population"
            description="A role appears once a run reports a stage under it. Widen the window, or clear the workflow filter to include chat sessions, whose turns are recorded as a main lane."
          />
        ) : null
      }
    >
      <div className="relative min-w-0 overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th scope="col" className="px-3 py-2 font-medium">
                Role
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Runs
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Corrections
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Avg corrections
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Gate failures
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Envelope failures
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Avg duration
              </th>
              <th scope="col" className="px-3 py-2 text-right font-medium">
                Avg cost
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {data?.map((row) => (
              <RoleRow key={roleLabel(row.role)} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </SectionShell>
  );
}

function RoleRow({ row }: { row: RoleStat }) {
  return (
    <tr className="transition-colors hover:bg-accent/40">
      <td className="px-3 py-2">
        <span
          className="font-mono text-xs font-medium"
          title={row.role === null ? UNATTRIBUTED_ROLE_HINT : undefined}
        >
          {roleLabel(row.role)}
        </span>
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{row.runs}</td>
      <td
        className={cn(
          'px-3 py-2 text-right font-mono text-xs tabular-nums',
          row.corrections > 0 && 'font-semibold',
        )}
      >
        {row.corrections}
      </td>
      <td className="px-3 py-2 text-right">
        <Average
          value={row.avg_corrections}
          count={row.stages}
          noun="stage"
          format={formatAverage}
        />
      </td>
      <td className="px-3 py-2 text-right">
        <Rate
          rate={row.gate_failure_rate}
          count={row.gate_checks}
          noun="check"
          className="justify-end"
        />
      </td>
      <td className="px-3 py-2 text-right">
        <Rate
          rate={row.envelope_failure_rate}
          count={row.envelope_attempts}
          noun="attempt"
          className="justify-end"
        />
      </td>
      <td className="px-3 py-2 text-right">
        <Average
          value={row.avg_duration_ms}
          count={row.timed_stages}
          noun="stage"
          format={formatMs}
        />
      </td>
      <td className="px-3 py-2 text-right">
        <Average
          value={row.avg_cost_usd}
          count={row.costed_stages}
          noun="stage"
          format={formatUsd}
        />
      </td>
    </tr>
  );
}
