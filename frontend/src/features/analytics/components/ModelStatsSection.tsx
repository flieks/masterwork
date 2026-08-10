import { useAtom } from 'jotai';
import { Cpu } from 'lucide-react';
import type { ModelStat } from '~/api/generated';
import { EmptyState } from '~/components/EmptyState';
import { cn } from '~/lib/utils';
import { modelStatsQueryAtom } from '../queries';
import {
  denominatorLabel,
  formatAverage,
  formatMs,
  formatTokens,
  formatUsd,
  isUnattributedModel,
  modelLabel,
  UNATTRIBUTED_MODEL_HINT,
} from '../stats';
import { Average, Rate } from './Rate';
import { SectionShell } from './SectionShell';

/**
 * Was the expensive model worth it.
 *
 * The `model: null` row is kept and sorted last by the API, and labelled here
 * rather than left blank — but it is not a model, and the note under the table
 * says why its acceptance rate looks so ordinary: it is the whole population's.
 */
export function ModelStatsSection() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(modelStatsQueryAtom);
  const hasUnattributed = data?.some(isUnattributedModel) ?? false;

  return (
    <SectionShell
      title="Models"
      description="Every model, through the lanes it ran. A stage's model is the model of the lane it ran in, and cost is summed over those lanes rather than the stages, because that is where a lane reports it."
      count={data ? denominatorLabel(data.length, 'row') : undefined}
      isPending={isPending}
      isError={isError}
      error={error}
      onRetry={refetch}
      empty={
        data?.length === 0 ? (
          <EmptyState
            icon={<Cpu className="size-8" />}
            title="No lane recorded in this population"
            description="A model appears once a run reports an agent lane. Widen the window or clear the workflow filter; a run that recorded no lane at all contributes nothing here."
          />
        ) : null
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <div className="relative min-w-0 overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="px-3 py-2 font-medium">
                  Model
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Runs
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Acceptance
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
                  Avg duration
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Tokens
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  Cost
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data?.map((row) => (
                <ModelRow key={modelLabel(row.model)} row={row} />
              ))}
            </tbody>
          </table>
        </div>
        {hasUnattributed ? <UnattributedNote /> : null}
      </div>
    </SectionShell>
  );
}

function ModelRow({ row }: { row: ModelStat }) {
  const unattributed = isUnattributedModel(row);

  return (
    <tr
      className={cn(
        'transition-colors hover:bg-accent/40',
        unattributed && 'bg-muted/20 text-muted-foreground',
      )}
    >
      <td className="px-3 py-2">
        <span className="flex items-center gap-1.5">
          <span className="font-mono text-xs font-medium">{modelLabel(row.model)}</span>
          {unattributed ? (
            <span
              title={UNATTRIBUTED_MODEL_HINT}
              className="rounded border border-dashed px-1 text-[10px] uppercase tracking-wide"
            >
              not a model
            </span>
          ) : null}
          <span className="text-[11px] text-muted-foreground">
            {denominatorLabel(row.lanes, 'lane')}
          </span>
        </span>
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{row.runs}</td>
      <td className="px-3 py-2 text-right">
        {/*
          Shown for the unattributed row too, but it is not a comparison: that
          row holds every run that had a git or checks lane, so its acceptance
          rate is the population's own. The badge and the note say so.
        */}
        <Rate
          rate={row.acceptance_rate}
          count={row.runs}
          noun="run"
          className={cn('justify-end', unattributed && 'opacity-70')}
        />
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{row.corrections}</td>
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
        <Average
          value={row.avg_duration_ms}
          count={row.timed_stages}
          noun="stage"
          format={formatMs}
        />
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">
        {formatTokens(row.tokens_in + row.tokens_out)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">
        {formatUsd(row.cost_usd)}
      </td>
    </tr>
  );
}

function UnattributedNote() {
  return (
    <p className="max-w-3xl text-xs leading-relaxed text-muted-foreground">
      <span className="font-mono">unattributed</span> is not a model. It is every lane that named
      none — including the pipeline&rsquo;s own <span className="font-mono">git</span> and{' '}
      <span className="font-mono">checks</span> lanes, which run no model and appear in every run.
      Each of those runs is therefore counted here <em>and</em> under the model that did its work,
      so this row double-counts runs by construction and its acceptance rate is the whole
      population&rsquo;s rather than a verdict on anything.
    </p>
  );
}
