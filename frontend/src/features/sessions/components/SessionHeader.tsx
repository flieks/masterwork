import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpFromLine,
  Bot,
  ChevronDown,
  ChevronRight,
  Coins,
  CornerUpLeft,
  DatabaseZap,
} from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { absoluteDateTime, formatDuration } from '~/lib/datetime';
import { formatTokens } from '~/lib/timeline';
import { cn } from '~/lib/utils';
import {
  isAutomatedSession,
  isSessionLive,
  runIdLabel,
  runTitleMeta,
  runWorkflow,
  sessionDetailPath,
  sessionLabel,
} from '../queries';
import { CostChip } from './CostChip';
import { DurationChip } from './DurationChip';
import { LiveIndicator } from './LiveIndicator';
import { RunStatusChip, StatChip } from './RunStatusChip';

/** The run's request, outcome and telemetry — the answer to "what was this?". */
export function SessionHeader({ session }: { session: CodingSession }) {
  const live = isSessionLive(session);
  const title = runTitleMeta(session);

  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <h1
          className={cn(
            'line-clamp-3 min-w-0 flex-1 text-xl font-semibold leading-snug tracking-tight',
            title.weak && 'font-mono text-lg font-normal italic text-muted-foreground',
          )}
          title={title.weak ? `Untitled run — showing ${title.text}` : title.text}
        >
          {title.text}
        </h1>
        <div className="flex shrink-0 items-center gap-2">
          {live ? <LiveIndicator /> : null}
          <RunStatusChip status={session.status} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
        <span title={session.id}>{runIdLabel(session)}</span>
        <span aria-hidden="true">·</span>
        <span>{runWorkflow(session)}</span>
        <span aria-hidden="true">·</span>
        <span>{sessionLabel(session)}</span>
        {session.model ? (
          <>
            <span aria-hidden="true">·</span>
            <span>{session.model}</span>
          </>
        ) : null}
        <span aria-hidden="true">·</span>
        <span>started {absoluteDateTime(session.started_at)}</span>
        {title.hint ? (
          <Badge variant="muted" className="gap-1 font-mono text-[10px] italic">
            {title.hint}
          </Badge>
        ) : null}
        {isAutomatedSession(session) ? (
          <Badge variant="secondary" className="gap-1 font-mono text-[10px]">
            <Bot className="size-3" />
            Automated
          </Badge>
        ) : null}
        {session.parent_session_id ? (
          <Link
            to={sessionDetailPath(session.parent_session_id)}
            className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
          >
            <CornerUpLeft className="size-3" aria-hidden="true" />
            parent run
          </Link>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <CostChip cost={session.cost_usd} className="text-xs" />
        <DurationChip session={session} className="text-xs" />
        <StatChip
          icon={Coins}
          label="Total tokens"
          value={formatTokens(session.tokens_total)}
          className="text-xs"
        />
        <StatChip
          icon={DatabaseZap}
          label="Cache-read tokens"
          value={formatTokens(session.cache_read_tokens)}
          className="text-xs"
        />
        <StatChip
          icon={ArrowUpFromLine}
          label="Output tokens"
          value={formatTokens(session.tokens_out)}
          className="text-xs"
        />
      </div>

      <p className="break-all font-mono text-[11px] text-muted-foreground" title={session.cwd}>
        {session.cwd || 'unknown working directory'}
      </p>

      <SessionStats stats={session.stats} />
    </header>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm font-medium" title={value}>
        {value}
      </dd>
    </div>
  );
}

type StatFormatter = (value: unknown) => string | null;

const count: StatFormatter = (v) => (typeof v === 'number' ? v.toLocaleString() : null);
const millis: StatFormatter = (v) => (typeof v === 'number' ? formatDuration(v / 1000) : null);

/**
 * `stats` is free-form: these keys get a label, anything else stays in the raw
 * JSON. Keys the backend promoted to columns of their own (cost and every token
 * count) are deliberately absent — they are already chips above.
 */
const KNOWN_STATS: Record<string, { label: string; format: StatFormatter }> = {
  turns: { label: 'Turns', format: count },
  num_turns: { label: 'Turns', format: count },
  duration_ms: { label: 'Wall time', format: millis },
  lines_added: { label: 'Lines added', format: count },
  lines_removed: { label: 'Lines removed', format: count },
  corrections: { label: 'Corrections', format: count },
};

function SessionStats({ stats }: { stats: Record<string, unknown> | null }) {
  const [expanded, setExpanded] = useState(false);
  if (!stats || Object.keys(stats).length === 0) return null;

  const known = Object.entries(stats).flatMap(([key, value]) => {
    const spec = KNOWN_STATS[key];
    if (!spec) return [];
    const formatted = spec.format(value);
    return formatted === null ? [] : [{ key, label: spec.label, value: formatted }];
  });

  return (
    <section className="rounded-lg border bg-muted/30 p-3">
      {known.length > 0 ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
          {known.map(({ key, label, value }) => (
            <Stat key={key} label={label} value={value} />
          ))}
        </dl>
      ) : null}

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className={cn(
          'flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground',
          known.length > 0 && 'mt-3',
        )}
      >
        {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        Raw stats
      </button>
      {expanded ? (
        <pre className="mt-1 max-h-64 overflow-auto rounded-md border bg-background p-3 text-xs leading-relaxed">
          {JSON.stringify(stats, null, 2)}
        </pre>
      ) : null}
    </section>
  );
}
