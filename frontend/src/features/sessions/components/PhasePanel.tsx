import { GitCommitHorizontal, ShieldCheck, ShieldX, X } from 'lucide-react';
import type { CodingPhase } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { absoluteDateTime } from '~/lib/datetime';
import { formatCost, formatSpan, formatTokens } from '~/lib/timeline';
import { cn } from '~/lib/utils';
import { laneTint } from '../lanes';
import { phaseStatusMeta } from '../status';
import { EventTimeline } from './EventTimeline';

/**
 * Why a marker phase looks empty, said out loud.
 *
 * Claude Code's tool hooks name no agent, so every tool call a subagent makes
 * is recorded on `main` — the only event that ever reaches the subagent's own
 * lane is the `SubagentStop` that ends it. Without that sentence the panel is a
 * row of dashes and reads as a bug rather than as a limit of what was recorded.
 */
const MARKER_NOTE =
  'Only the end of this span was recorded, so it is drawn as a moment. The tool calls made ' +
  'inside it were attributed to the main lane — hook events do not say which agent ran them — ' +
  'which is why nothing but the closing event is listed here.';

/** A span with no measured length: an instant on the chart, a marker here. */
function isMarkerPhase(phase: CodingPhase): boolean {
  return phase.duration_ms === 0;
}

/** What one phase cost, proved, and did — with its own slice of the event stream. */
export function PhasePanel({
  sessionId,
  phase,
  onClose,
}: {
  sessionId: string;
  phase: CodingPhase;
  onClose: () => void;
}) {
  const meta = phaseStatusMeta(phase.status);
  const Icon = meta.icon;
  const tint = phase.agent ? laneTint({ name: phase.agent }) : null;
  const marker = isMarkerPhase(phase);

  return (
    <section
      aria-label="Phase detail"
      className="flex flex-col gap-4 rounded-lg border bg-card p-4"
    >
      <header className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">
              <span className="font-mono text-muted-foreground">{phase.seq}. </span>
              {phase.name}
            </h3>
            {/* A status that needs explaining explains itself — `abandoned` is
                not a failure, and nothing else on the panel would say so. */}
            <Badge
              className={cn('gap-1 border-transparent capitalize', meta.chip)}
              title={meta.hint}
            >
              <Icon className={cn('size-3', meta.spin && 'animate-spin')} />
              {phase.status}
            </Badge>
            {phase.agent ? (
              <span
                className="font-mono text-[11px] font-medium"
                style={tint ? { color: tint.text } : undefined}
              >
                {phase.agent}
              </span>
            ) : null}
            {phase.kind ? (
              <Badge variant="muted" className="font-mono text-[10px]">
                {phase.kind}
              </Badge>
            ) : null}
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            started {absoluteDateTime(phase.started_at)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close phase"
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </header>

      {phase.description ? (
        <p className="whitespace-pre-wrap text-sm text-muted-foreground">{phase.description}</p>
      ) : null}

      {marker ? (
        <p className="rounded-md border border-dashed bg-muted/30 p-3 text-sm text-muted-foreground">
          {MARKER_NOTE}
        </p>
      ) : null}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
        {/* `0ms` is a measurement nobody made — a marker's length is unknown. */}
        <Fact label="Duration" value={marker ? 'not recorded' : formatSpan(phase.duration_ms)} />
        <Fact label="Cost" value={formatCost(phase.cost_usd)} />
        <Fact label="Tokens in" value={formatTokens(phase.tokens_in)} />
        <Fact label="Tokens out" value={formatTokens(phase.tokens_out)} />
        <Fact label="Corrections" value={String(phase.corrections)} />
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="muted" className="gap-1">
          <ShieldCheck className="size-3 text-emerald-600 dark:text-emerald-400" />
          {phase.gates_passed} gate{phase.gates_passed === 1 ? '' : 's'} passed
        </Badge>
        <Badge
          variant="muted"
          className={cn(
            'gap-1',
            phase.gates_failed > 0 && 'bg-red-500/15 text-red-700 dark:text-red-400',
          )}
        >
          <ShieldX className="size-3" />
          {phase.gates_failed} failed
        </Badge>
        {phase.commit_sha ? (
          <Badge variant="outline" className="gap-1 font-mono text-[11px]" title={phase.commit_sha}>
            <GitCommitHorizontal className="size-3" />
            {phase.commit_sha.slice(0, 7)}
          </Badge>
        ) : null}
      </div>

      <div className="border-t pt-3">
        <h4 className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
          Events in this phase
        </h4>
        <EventTimeline sessionId={sessionId} phaseId={phase.id} />
      </div>
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-sm font-medium" title={value}>
        {value}
      </dd>
    </div>
  );
}
