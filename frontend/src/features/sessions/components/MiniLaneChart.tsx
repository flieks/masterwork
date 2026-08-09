import type { CodingSession } from '~/api/generated';
import { buildTimeScale, layoutLane, runWindow, scaleTicks } from '~/lib/timeline';
import { buildLanes, implicitLane, laneTint } from '../lanes';
import { phaseStatusMeta } from '../status';

/** A short phase still has to be visible on a card-sized track. */
const MIN_BAR_PCT = 0.75;

/**
 * The lane keeps its height and splits it between sub-rows, so a run with one
 * phase per lane looks exactly as it did and a dense one reads as a stack of
 * stripes rather than a smear of bars painted over each other.
 */
const MAX_LANE_ROWS = 3;

/**
 * The card's signature element: the run's own time axis, one row per agent
 * lane, each phase a bar spanning its real start → end.
 *
 * The bars carry no per-event dots here on purpose — the list endpoint returns
 * `PhaseSummary` only, and fetching every card's event stream on every 2.5s
 * poll would cost one request per card. The detail waterfall, which already
 * holds the stream, draws the tool-call ticks.
 */
export function MiniLaneChart({ session, now }: { session: CodingSession; now: number }) {
  const axis = runWindow(session, now);
  const lanes = buildLanes(session.agents, session.phases);
  // The card collapses idle time for the same reason the detail chart does: a
  // 36-hour run's two hours of work are the whole point of the sparkline.
  const scale = buildTimeScale(
    axis,
    lanes.map(({ phases }) => phases),
  );
  // A card is a third the width of the detail chart, so its labels need
  // three times the clearance before two of them read as one.
  const ticks = scaleTicks(scale, 3, 22);

  return (
    <div className="flex flex-col gap-1" aria-label="Run timeline">
      <div className="flex items-end gap-2">
        <span className="w-14 shrink-0" />
        <div className="relative h-3 min-w-0 flex-1 border-b border-dashed">
          {scale.breaks.map((gap) => (
            <span
              key={gap.startMs}
              aria-hidden="true"
              className="absolute inset-y-0 border-x border-dashed border-border bg-muted/40"
              style={{ left: `${gap.leftPct}%`, width: `${gap.widthPct}%` }}
            />
          ))}
          {ticks.map((tick) => (
            <span
              key={tick.atMs}
              className="absolute bottom-0 font-mono text-[9px] leading-none text-muted-foreground"
              style={{
                left: `${tick.atPct}%`,
                transform: tick.atPct > 0 ? 'translateX(-50%)' : '',
              }}
            >
              {tick.label}
            </span>
          ))}
        </div>
      </div>

      {lanes.length === 0 ? (
        <EmptyLaneRow />
      ) : (
        lanes.map(({ lane, phases }) => {
          const tint = laneTint(lane);
          const layout = layoutLane(scale, phases, {
            minWidthPct: MIN_BAR_PCT,
            maxRows: MAX_LANE_ROWS,
          });
          return (
            <div key={lane.name} className="flex items-center gap-2">
              <span
                className="w-14 shrink-0 truncate text-[10px] font-medium leading-none"
                style={{ color: tint.text }}
                title={lane.name}
              >
                {lane.name}
              </span>
              <div className="relative h-2.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-muted/60">
                {phases.map((phase, i) => {
                  const span = layout.spans[i];
                  const failed = phaseStatusMeta(phase.status).error;
                  return (
                    <span
                      key={phase.seq}
                      title={`${phase.name} — ${phase.status}`}
                      data-phase-bar={phase.name}
                      className="absolute rounded-[3px] border"
                      style={{
                        left: `${span.leftPct}%`,
                        // An untimed phase is a tick, not a bar the card can size.
                        width: span.instant ? '2px' : `${span.widthPct}%`,
                        top: `${(span.row / layout.rows) * 100}%`,
                        height: `${100 / layout.rows}%`,
                        background: failed ? 'hsl(0 72% 51% / 0.22)' : tint.fill,
                        borderColor: failed ? 'hsl(0 72% 51% / 0.55)' : tint.border,
                      }}
                    />
                  );
                })}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

/** A run that reported no lane and no phase is still a run — show the gap. */
function EmptyLaneRow() {
  const tint = laneTint(implicitLane());
  return (
    <div className="flex items-center gap-2">
      <span
        className="w-14 shrink-0 truncate text-[10px] font-medium leading-none"
        style={{ color: tint.text }}
      >
        session
      </span>
      <div className="relative h-2.5 min-w-0 flex-1 rounded-sm bg-muted/60">
        <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 border-t border-dashed" />
      </div>
    </div>
  );
}
