import type { CodingEvent, CodingPhase, CodingSessionDetail } from '~/api/generated';
import {
  buildTimeScale,
  contextUsedPct,
  formatSpan,
  layoutLane,
  placePoint,
  runWindow,
  scaleTicks,
  type LaneLayout,
  type LaneSpan,
  type RunWindow,
  type ScaleBreak,
  type TimeScale,
} from '~/lib/timeline';
import { useElementWidth } from '~/lib/hooks';
import { cn } from '~/lib/utils';
import { buildLanes, implicitLane, laneTint, type LaneTint } from '../lanes';
import { isToolCallEvent } from '../queries';
import { phaseStatusMeta } from '../status';

/**
 * A 87ms phase still has to be clickable, so every block gets a floor — and the
 * floor the packer reserves has to be the floor that renders, or blocks spaced
 * to clear each other in percent still overlap in pixels. Hence the measured
 * track: the pixel minimum is converted to a percentage of the real width and
 * then used for both. It stays small on purpose — a 30-hour axis carries turns
 * worth 0.1% each, and a floor wide enough to fit a label would collide them
 * all. Sub-rows, not width, are what keeps a dense lane readable.
 */
const MIN_BLOCK_PX = 20;

/** Before the track is measured, and as the bounds of what measuring can say. */
const MIN_BLOCK_PCT = 0.5;
const MAX_BLOCK_FLOOR_PCT = 3;

/** Below this a block has no room for its label, so it drops its padding too —
 *  padding is a pixel width the packer cannot see, and 16px of it on a 20px
 *  block is 16px of overlap. */
const CRAMPED_BLOCK_PX = 56;

/** Past this a lane is taller than it is worth; the packer reuses rows instead. */
const MAX_LANE_ROWS = 6;

/**
 * A track narrower than this cannot label its blocks whatever the axis does, so
 * below it the chart scrolls sideways instead of shrinking. Both classes have
 * to agree: the rows size themselves, and the wrapper stops the sticky rail
 * from collapsing the scrollable width to the viewport.
 */
const TRACK_WIDTH = 'w-[calc(100vw-24rem)] min-w-[52rem]';
const TRACK_MIN_WIDTH_WRAPPER = 'w-max';

function blockFloorPct(trackWidth: number): number {
  if (trackWidth <= 0) return MIN_BLOCK_PCT;
  const pct = (MIN_BLOCK_PX / trackWidth) * 100;
  return Math.min(Math.max(pct, MIN_BLOCK_PCT), MAX_BLOCK_FLOOR_PCT);
}

/**
 * Track geometry, in rem: one sub-row is a band, padded away from its lane.
 * A lane deep enough to need this many rows holds blocks too narrow to label,
 * so its rows shrink to the height of a bar — six tall bands would make one
 * dense session taller than the screen for nothing.
 */
const ROW_HEIGHT = 2.5;
const THIN_ROW_HEIGHT = 1.25;
const THIN_FROM_ROWS = 3;
const ROW_GAP = 0.25;
const TRACK_PADDING = 0.5;

interface LaneGeometry {
  rowHeight: number;
  height: string;
  offset: (row: number) => string;
}

function laneGeometry(rows: number): LaneGeometry {
  const rowHeight = rows >= THIN_FROM_ROWS ? THIN_ROW_HEIGHT : ROW_HEIGHT;
  return {
    rowHeight,
    height: `${TRACK_PADDING * 2 + rows * rowHeight + (rows - 1) * ROW_GAP}rem`,
    offset: (row) => `${TRACK_PADDING + row * (rowHeight + ROW_GAP)}rem`,
  };
}

interface RunWaterfallProps {
  session: CodingSessionDetail;
  events: CodingEvent[];
  now: number;
  selectedPhaseId: number | null;
  onSelectPhase: (phaseId: number) => void;
}

/**
 * The run on one shared time axis: a rail of agent lanes on the left, each
 * lane's phases placed and sized by when they actually happened on the right.
 */
export function RunWaterfall({
  session,
  events,
  now,
  selectedPhaseId,
  onSelectPhase,
}: RunWaterfallProps) {
  const [trackRef, trackWidth] = useElementWidth<HTMLDivElement>();
  const axis = runWindow(session, now);
  const lanes = buildLanes(session.agents, session.phases);
  const scale = buildTimeScale(
    axis,
    lanes.map(({ phases }) => phases),
  );
  const ticks = scaleTicks(scale, 6);
  const minWidthPct = blockFloorPct(trackWidth);
  const toolCalls = events.filter(isToolCallEvent);
  const byPhase = new Map<number, CodingEvent[]>();
  for (const event of toolCalls) {
    if (event.phase_id === null) continue;
    const held = byPhase.get(event.phase_id);
    if (held) held.push(event);
    else byPhase.set(event.phase_id, [event]);
  }

  return (
    // One scroll container for every row, so the tracks stay in step and the
    // rail can pin itself to the left edge while they move under it.
    <div className="overflow-x-auto rounded-lg border" aria-label="Run waterfall">
      <div className={cn('min-w-max', TRACK_MIN_WIDTH_WRAPPER)}>
        <div className="flex border-b bg-muted/30">
          <div className="sticky left-0 z-20 w-44 shrink-0 border-r bg-muted px-3 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
            Lanes
          </div>
          {/* Every lane track is this same column, so one measurement fits all. */}
          <div ref={trackRef} className={cn('relative h-6', TRACK_WIDTH)}>
            {scale.breaks.map((gap) => (
              <BreakBand key={gap.startMs} gap={gap} />
            ))}
            {ticks.map((tick) => (
              <span
                key={tick.atMs}
                className="absolute top-1.5 font-mono text-[10px] leading-none text-muted-foreground"
                style={{
                  left: `${tick.atPct}%`,
                  transform: tick.atPct > 0 ? 'translateX(-50%)' : undefined,
                }}
              >
                {tick.label}
              </span>
            ))}
          </div>
        </div>

        {lanes.length === 0 ? (
          <ImplicitLaneRow session={session} scale={scale} events={toolCalls} />
        ) : (
          lanes.map(({ lane, phases }) => {
            const tint = laneTint(lane);
            const layout = layoutLane(scale, phases, { minWidthPct, maxRows: MAX_LANE_ROWS });
            const geometry = laneGeometry(layout.rows);
            return (
              <div key={lane.name} data-lane={lane.name} className="flex border-b last:border-b-0">
                <LaneRail lane={lane} phases={phases} layout={layout} tint={tint} />
                <div
                  className={cn('relative min-h-[3.75rem] overflow-hidden', TRACK_WIDTH)}
                  style={{ height: geometry.height }}
                >
                  <AxisGuides ticks={ticks.map((t) => t.atPct)} />
                  {scale.breaks.map((gap) => (
                    <BreakBand key={gap.startMs} gap={gap} />
                  ))}
                  {phases.map((phase, i) => (
                    <PhaseBlock
                      key={phase.id}
                      phase={phase}
                      span={layout.spans[i]}
                      geometry={geometry}
                      floorPct={minWidthPct}
                      trackWidth={trackWidth}
                      tint={tint}
                      events={byPhase.get(phase.id) ?? []}
                      selected={phase.id === selectedPhaseId}
                      onSelect={() => onSelectPhase(phase.id)}
                    />
                  ))}
                </div>
              </div>
            );
          })
        )}

        {scale.breaks.length > 0 ? (
          <div className="sticky left-0 w-max border-t bg-muted/20 px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
            {scale.breaks.length} idle {scale.breaks.length === 1 ? 'gap' : 'gaps'} cut from the
            axis · {formatSpan(scale.skippedMs)} skipped · hatched bands are not to scale
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Where the axis stops being to scale. Hatched, not blank — blank would read as
 * "nothing ran here at this scale", and the point is that the scale itself
 * breaks. The band is too narrow for a legible label, so the hours it stands
 * for live in its tooltip and in the caption under the chart.
 */
function BreakBand({ gap }: { gap: ScaleBreak }) {
  return (
    <span
      title={`${formatSpan(gap.skippedMs)} with nothing running — cut out of the axis`}
      className="absolute inset-y-0 border-x border-dashed border-border bg-[repeating-linear-gradient(135deg,hsl(0_0%_50%/0.1)_0_4px,transparent_4px_9px)]"
      style={{ left: `${gap.leftPct}%`, width: `${gap.widthPct}%` }}
    />
  );
}

/** Faint verticals under the blocks so a bar can be read against the axis. */
function AxisGuides({ ticks }: { ticks: number[] }) {
  return (
    <>
      {ticks.map((pct) => (
        <span
          key={pct}
          aria-hidden="true"
          className="absolute inset-y-0 w-px bg-border/60"
          style={{ left: `${pct}%` }}
        />
      ))}
    </>
  );
}

function LaneRail({
  lane,
  phases,
  layout,
  tint,
}: {
  lane: {
    name: string;
    model: string | null;
    context_tokens: number | null;
    context_window: number | null;
  };
  phases: CodingPhase[];
  layout: LaneLayout;
  tint: LaneTint;
}) {
  const { spans } = layout;
  const context = contextUsedPct(lane.context_tokens, lane.context_window);
  // No model means the lane is not an agent — the phase's kind says what it is.
  // An empty lane says so outright: it was declared but nothing timed it, and a
  // blank track reads as "this agent never ran", which is a different claim.
  // A lane whose spans are all instants makes the weaker claim it can support:
  // we know when each one ran, and nothing recorded how long it took.
  const untimed = phases.length > 0 && spans.every((span) => span.instant);
  const subtitle = phases.length === 0 ? 'nothing recorded' : untimed ? 'start times only' : null;
  const model = lane.model ?? phases.find((p) => p.kind)?.kind ?? '—';
  const subtitleHint =
    phases.length === 0
      ? 'This lane was declared but no stage was ever recorded against it.'
      : 'Every diamond is when one ran. How long each took was never recorded, so there is nothing to draw a bar from — no spawn event arrived: a pre-hook session, an internal agent, or a resumed agent stopping again.';

  return (
    <div className="sticky left-0 z-10 w-44 shrink-0 border-r bg-card px-3 py-2.5">
      <div
        className="truncate text-xs font-semibold"
        style={{ color: tint.text }}
        title={lane.name}
      >
        {lane.name}
      </div>
      {subtitle === null ? (
        <div className="truncate font-mono text-[10px] text-muted-foreground" title={model}>
          {model}
        </div>
      ) : (
        <div
          className="truncate font-mono text-[10px] text-muted-foreground underline decoration-dotted underline-offset-2"
          title={subtitleHint}
        >
          {subtitle}
        </div>
      )}
      {/* The stacking has a ceiling, and a lane that hit it is drawing some of
          its phases over each other. Say so rather than let it read as truth. */}
      {layout.crowded ? (
        <div
          className="truncate font-mono text-[10px] text-amber-700 dark:text-amber-500"
          title={`More phases start at once than ${layout.rows} rows can separate — some blocks overlap. Click one to read it in the panel below.`}
        >
          {layout.rows} rows · some overlap
        </div>
      ) : null}
      {context !== null ? (
        <div className="mt-1.5">
          <div className="flex items-baseline justify-between font-mono text-[9px] uppercase tracking-wide text-muted-foreground">
            <span>Context</span>
            <span>{context}%</span>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full"
              style={{ width: `${context}%`, background: tint.text }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PhaseBlock({
  phase,
  span,
  geometry,
  floorPct,
  trackWidth,
  tint,
  events,
  selected,
  onSelect,
}: {
  phase: CodingPhase;
  span: LaneSpan;
  geometry: LaneGeometry;
  /** The width the packer reserved — what a marker, which has none, draws as. */
  floorPct: number;
  trackWidth: number;
  tint: LaneTint;
  events: CodingEvent[];
  selected: boolean;
  onSelect: () => void;
}) {
  const { leftPct, widthPct, durationMs } = span;
  const meta = phaseStatusMeta(phase.status);
  const Icon = meta.icon;
  const corrected = phase.corrections > 0;
  const alarming = meta.error || phase.gates_failed > 0 || corrected;
  const title = [
    `${phase.name} — ${phase.status}`,
    span.instant ? 'duration not recorded' : formatSpan(durationMs),
    span.truncated ? 'end not recorded — cut to the next turn on this lane' : null,
    phase.description,
  ]
    .filter(Boolean)
    .join(' · ');

  if (span.instant) {
    return (
      <PhaseMarker
        phase={phase}
        span={span}
        geometry={geometry}
        floorPct={floorPct}
        tint={tint}
        alarming={alarming}
        selected={selected}
        onSelect={onSelect}
        title={title}
      />
    );
  }

  // Ticks are placed inside the phase's own span, not the run's.
  const startMs = new Date(phase.started_at).getTime();
  const phaseAxis: RunWindow = {
    startMs,
    endMs: startMs + Math.max(durationMs, 1),
    durationMs: Math.max(durationMs, 1),
  };

  // A block that ends with the run is anchored to the right edge: the pixel
  // floor below is wider than its percentage, and growing rightwards would
  // push it out of the (clipped) track.
  const endsWithRun = leftPct + widthPct > 99;
  const anchor = endsWithRun
    ? { right: `${Math.max(100 - leftPct - widthPct, 0)}%` }
    : { left: `${leftPct}%` };

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Phase ${phase.name}`}
      title={title}
      className={cn(
        // No CSS width floor: the percentage already carries one, and a pixel
        // minimum the packer cannot see is exactly what makes blocks overlap.
        '@container absolute overflow-hidden rounded-md border py-1 text-left transition-shadow',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected && 'ring-2 ring-ring',
        // An end nobody reported is a guess at the edge — say so in the border.
        span.truncated && 'border-dashed',
      )}
      style={{
        ...anchor,
        top: geometry.offset(span.row),
        height: `${geometry.rowHeight}rem`,
        width: `${widthPct}%`,
        paddingInline: (widthPct / 100) * trackWidth < CRAMPED_BLOCK_PX ? 0 : '0.5rem',
        background: alarming ? 'hsl(0 72% 51% / 0.14)' : tint.fill,
        borderColor: alarming ? 'hsl(0 72% 51% / 0.5)' : tint.border,
      }}
    >
      <span className="flex items-center gap-1.5">
        <Icon
          className={cn('size-3 shrink-0', meta.spin && 'animate-spin')}
          style={{ color: alarming ? undefined : tint.text }}
        />
        <span className="truncate text-xs font-medium">{phase.name}</span>
        {/* Clipped telemetry lies — "113ms" cut to "113m" reads as minutes. Below the
            width that fits them whole, drop the label rather than truncate it; the
            tooltip and the phase panel still carry the full values. */}
        <span className="ml-auto hidden shrink-0 font-mono text-[10px] text-muted-foreground @min-[7rem]:inline">
          {formatSpan(durationMs)}
        </span>
      </span>
      {phase.description ? (
        <span className="mt-0.5 hidden truncate text-[10px] leading-tight text-muted-foreground @min-[10rem]:block">
          {phase.description}
        </span>
      ) : null}

      {events.length > 0 ? (
        <span
          className="absolute inset-x-1 bottom-0.5 block h-1.5"
          aria-label={`${events.length} tool calls`}
        >
          {events.map((event) => (
            <span
              key={event.id}
              className="absolute bottom-0 h-1.5 w-px bg-current opacity-40"
              style={{ left: `${placePoint(phaseAxis, event.created_at)}%` }}
            />
          ))}
        </span>
      ) : null}
    </button>
  );
}

/**
 * A phase nobody timed. Drawing it as a bar would invent a length it never had
 * — and a lane of 28 of them, each inflated to the width floor, is the pile-up
 * this diamond exists to avoid. It stays a button: the phase panel below still
 * has its events, its gates and its cost.
 */
function PhaseMarker({
  phase,
  span,
  geometry,
  floorPct,
  tint,
  alarming,
  selected,
  onSelect,
  title,
}: {
  phase: CodingPhase;
  span: LaneSpan;
  geometry: LaneGeometry;
  floorPct: number;
  tint: LaneTint;
  alarming: boolean;
  selected: boolean;
  onSelect: () => void;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`Phase ${phase.name}`}
      data-phase-marker={phase.name}
      title={title}
      className={cn(
        'absolute flex items-center justify-center rounded-sm transition-shadow',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected && 'ring-2 ring-ring',
      )}
      style={{
        // Exactly the slot the packer reserved — the diamond sits inside it, so
        // two moments the packer separated cannot touch on screen.
        left: `${Math.min(span.leftPct, 100 - floorPct)}%`,
        width: `${floorPct}%`,
        top: geometry.offset(span.row),
        height: `${geometry.rowHeight}rem`,
      }}
    >
      <span
        aria-hidden="true"
        className="size-2 rotate-45 border"
        style={{
          background: alarming ? 'hsl(0 72% 51% / 0.3)' : tint.fill,
          borderColor: alarming ? 'hsl(0 72% 51% / 0.7)' : tint.border,
        }}
      />
    </button>
  );
}

/**
 * A session that reported neither lane nor phase still has events. Give it one
 * row and scatter its tool calls along the axis — the shape of the run is the
 * only thing there is to show.
 */
function ImplicitLaneRow({
  session,
  scale,
  events,
}: {
  session: CodingSessionDetail;
  scale: TimeScale;
  events: CodingEvent[];
}) {
  const lane = implicitLane(session.model ?? 'session');
  const tint = laneTint(lane);

  return (
    <div className="flex">
      <div className="sticky left-0 z-10 w-44 shrink-0 border-r bg-card px-3 py-2.5">
        <div className="truncate text-xs font-semibold" style={{ color: tint.text }}>
          {lane.name}
        </div>
        <div className="font-mono text-[10px] text-muted-foreground">no phases reported</div>
      </div>
      <div className={cn('relative min-h-[3.75rem] overflow-hidden', TRACK_WIDTH)}>
        <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 border-t border-dashed" />
        {events.map((event) => (
          <span
            key={event.id}
            className="absolute top-1/2 h-3 w-px -translate-y-1/2"
            style={{
              left: `${scale.project(new Date(event.created_at).getTime())}%`,
              background: tint.text,
            }}
          />
        ))}
      </div>
    </div>
  );
}
