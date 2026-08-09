import { Link } from 'react-router-dom';
import { Bot, Coins, GitBranch } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { Card } from '~/components/ui/card';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { formatTokens } from '~/lib/timeline';
import { cn } from '~/lib/utils';
import { assetUseKey, mergeAssetUses } from '../assets';
import {
  isAutomatedSession,
  isSessionLive,
  runIdLabel,
  runTitleMeta,
  runWorkflow,
  sessionDetailPath,
} from '../queries';
import { stageRunsLabel } from '../runs';
import { phaseStatusMeta } from '../status';
import { AssetChip } from './AssetChip';
import { CostChip } from './CostChip';
import { DurationChip } from './DurationChip';
import { LiveIndicator } from './LiveIndicator';
import { MiniLaneChart } from './MiniLaneChart';
import { RunStatusChip, StatChip } from './RunStatusChip';

/** A pipeline run and a chat session are the same object here — one grid, no sections. */
const WORKFLOW_TINT: Record<string, string> = {
  factory: 'text-violet-700 dark:text-violet-400',
  chat: 'text-sky-700 dark:text-sky-400',
};

/** Enough to see the shape of what a run used; the detail page has them all. */
const MAX_CARD_ASSETS = 4;

export function RunCard({ session, now = Date.now() }: { session: CodingSession; now?: number }) {
  const live = isSessionLive(session, now);
  const workflow = runWorkflow(session);
  const title = runTitleMeta(session);

  return (
    <Link
      to={sessionDetailPath(session.id)}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Card className="flex h-full flex-col gap-3 p-4 transition-colors hover:border-ring hover:bg-accent/40">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            {/* The request is what a human recognises a run by; the id is how
                they refer to it afterwards, so it sits underneath. */}
            <p
              className={cn(
                'line-clamp-2 text-sm font-semibold leading-snug',
                title.weak
                  ? 'font-mono font-normal italic text-muted-foreground'
                  : 'text-foreground',
              )}
              title={title.weak ? `Untitled run — showing ${title.text}` : title.text}
            >
              {title.text}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] text-muted-foreground">
              <span title={session.id}>{runIdLabel(session)}</span>
              <span aria-hidden="true">·</span>
              <span className={WORKFLOW_TINT[workflow] ?? undefined}>{workflow}</span>
              {title.hint ? (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="italic">{title.hint}</span>
                </>
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {isAutomatedSession(session) ? (
              <Bot className="size-3.5 text-muted-foreground" aria-label="Automated run" />
            ) : null}
            {live ? <LiveIndicator /> : null}
          </div>
        </div>

        <MiniLaneChart session={session} now={now} />

        <div className="mt-auto flex items-center gap-2 pt-1">
          <RunStatusChip status={session.status} />
          <PhaseDots session={session} />
          <time
            className="ml-auto shrink-0 text-[11px] text-muted-foreground"
            dateTime={session.started_at}
            title={absoluteDateTime(session.started_at)}
          >
            {relativeTime(session.started_at)}
          </time>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <CostChip cost={session.cost_usd} />
          <DurationChip session={session} />
          <StatChip icon={Coins} label="Total tokens" value={formatTokens(session.tokens_total)} />
          {session.child_count > 0 ? (
            <span
              title="This run launched a headless run per pipeline stage — open it to see them"
              className="relative inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              <GitBranch className="size-3 shrink-0" aria-hidden="true" />
              {stageRunsLabel(session.child_count)}
            </span>
          ) : null}
        </div>

        <CardAssets assets={session.assets} />
      </Card>
    </Link>
  );
}

/**
 * The run's skills and agents at a glance. Not links: the card is already one,
 * and nesting anchors is invalid HTML — the detail page carries the real links.
 */
function CardAssets({ assets }: { assets: CodingSession['assets'] }) {
  // The card has no lanes, so the API's per-lane rows collapse to one per name.
  const merged = mergeAssetUses(assets);
  if (merged.length === 0) return null;
  const shown = merged.slice(0, MAX_CARD_ASSETS);
  const rest = merged.length - shown.length;

  return (
    <div className="flex flex-wrap items-center gap-1" aria-label="Assets used">
      {shown.map((asset) => (
        <AssetChip key={assetUseKey(asset)} asset={asset} asLink={false} />
      ))}
      {rest > 0 ? <span className="text-[11px] text-muted-foreground">+{rest} more</span> : null}
    </div>
  );
}

/**
 * One dot per phase, in run order, coloured by how that phase ended.
 *
 * Capped: a 58-turn chat session is 570px of dots on a 400px card, and every
 * dot is `shrink-0`, so an uncapped row walks straight out of the border. The
 * counter is not just "+N" — a failure hidden behind it would be a failure the
 * grid never showed, so the overflow carries the worst status it swallowed.
 */
const MAX_CARD_PHASE_DOTS = 8;

function PhaseDots({ session }: { session: CodingSession }) {
  if (session.phases.length === 0) return null;
  const shown = session.phases.slice(0, MAX_CARD_PHASE_DOTS);
  const hidden = session.phases.slice(MAX_CARD_PHASE_DOTS);
  const hiddenFailed = hidden.some((phase) => phaseStatusMeta(phase.status).error);

  return (
    <span
      // Every dot is shrink-0, so without this the row grows into the
      // timestamp instead of giving way to it on a narrow card.
      className="flex min-w-0 items-center gap-1 overflow-hidden"
      aria-label={`${session.phases.length} phases`}
    >
      {shown.map((phase) => (
        <span
          key={phase.seq}
          title={`${phase.name} — ${phase.status}`}
          data-phase-dot={phase.status}
          className={cn('size-1.5 shrink-0 rounded-full', phaseStatusMeta(phase.status).dot)}
        />
      ))}
      {hidden.length > 0 ? (
        <span
          data-phase-dots-overflow={hiddenFailed ? 'failed' : 'ok'}
          title={
            hiddenFailed
              ? `${hidden.length} more phases, at least one failed — open the run to see them`
              : `${hidden.length} more phases — open the run to see them`
          }
          className={cn(
            'shrink-0 font-mono text-[10px] leading-none',
            hiddenFailed ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground',
          )}
        >
          +{hidden.length}
        </span>
      ) : null}
    </span>
  );
}
