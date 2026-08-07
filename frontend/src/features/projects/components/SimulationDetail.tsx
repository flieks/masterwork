import { useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowDown,
  ArrowUp,
  Check,
  CheckCircle2,
  CircleDashed,
  ClipboardCheck,
  FileCode2,
  Loader2,
  MinusCircle,
  CircleSlash,
  TriangleAlert,
  Wand2,
  XCircle,
} from 'lucide-react';
import type { Simulation, SimulationChecklistItem, SimulationSuggestion } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { MarkdownView } from '~/components/MarkdownView';
import { MermaidView } from '~/components/MermaidView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDate } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { applySuggestionMutationAtom } from '../simulationQueries';

export function scoreColor(score: number): string {
  if (score >= 80) return 'text-emerald-600 dark:text-emerald-400';
  if (score >= 60) return 'text-amber-600 dark:text-amber-400';
  return 'text-red-600 dark:text-red-400';
}

export function SimulationDetail({
  simulation,
  previous = null,
}: {
  simulation: Simulation;
  previous?: Simulation | null;
}) {
  // Persists across run switches; falls back per-run when that tab has no content.
  const [activeTab, setActiveTab] = useState<string | null>(null);

  if (simulation.status === 'running') {
    return (
      <section className="flex items-center gap-3 rounded-md border border-dashed p-6 text-sm text-muted-foreground">
        <Loader2 className="size-5 animate-spin" />
        Simulation in progress — Claude is reading the linked assets and walking the scenario…
      </section>
    );
  }

  // Interrupted ≠ failed: the run was cut short (restart, or stopped by hand),
  // which says nothing about the assets. Keep it neutral so it doesn't read as
  // a problem with the toolkit.
  if (simulation.status === 'interrupted') {
    return (
      <section className="flex items-start gap-3 rounded-md border bg-muted/40 p-4">
        <CircleSlash className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 text-sm">
          <p className="font-medium">This simulation was interrupted</p>
          <p className="mt-1 text-muted-foreground">
            {simulation.error ?? 'The run ended before it finished.'} Nothing is wrong with your
            assets — run it again to get a score.
          </p>
        </div>
      </section>
    );
  }

  if (simulation.status === 'failed') {
    return (
      <section className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-4">
        <TriangleAlert className="mt-0.5 size-5 shrink-0 text-destructive" />
        <div className="min-w-0 text-sm">
          <p className="font-medium">This simulation failed</p>
          <p className="mt-1 text-muted-foreground">{simulation.error ?? 'Unknown error.'}</p>
        </div>
      </section>
    );
  }

  const hasChecklist = simulation.checklist.length > 0;
  const hasSummary = Boolean(simulation.summary?.trim());
  const hasTrace = Boolean(simulation.trace_mermaid?.trim());
  const hasAnalysis = Boolean(simulation.analysis?.trim());
  const hasScenario = Boolean(simulation.scenario.trim());
  // Checklist is the headline of a run — it explains the score — so lead with it.
  const defaultTab = hasChecklist
    ? 'checklist'
    : hasSummary
      ? 'summary'
      : hasTrace
        ? 'trace'
        : hasAnalysis
          ? 'analysis'
          : 'suggestions';

  const available: Record<string, boolean> = {
    checklist: hasChecklist,
    summary: hasSummary,
    trace: hasTrace,
    analysis: hasAnalysis,
    suggestions: true,
    scenario: true,
  };
  const tab = activeTab && available[activeTab] ? activeTab : defaultTab;

  return (
    <div className="space-y-4">
      <ScoreHeader simulation={simulation} previous={previous} />

      <Tabs value={tab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="checklist" disabled={!hasChecklist}>
            Checklist
            {hasChecklist && (
              <span className="rounded-full bg-muted-foreground/15 px-1.5 text-xs tabular-nums">
                {simulation.checklist.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="summary" disabled={!hasSummary}>
            What it did
          </TabsTrigger>
          <TabsTrigger value="trace" disabled={!hasTrace}>
            Execution trace
          </TabsTrigger>
          <TabsTrigger value="analysis" disabled={!hasAnalysis}>
            Analysis
          </TabsTrigger>
          <TabsTrigger value="suggestions">
            Suggestions
            {simulation.suggestions.length > 0 && (
              <span className="rounded-full bg-muted-foreground/15 px-1.5 text-xs tabular-nums">
                {simulation.suggestions.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="scenario">Scenario</TabsTrigger>
        </TabsList>

        <TabsContent value="checklist" className="pt-4">
          <ChecklistPanel checklist={simulation.checklist} previous={previous} />
        </TabsContent>

        <TabsContent value="summary" className="pt-4">
          {simulation.summary && <MarkdownView content={simulation.summary} />}
        </TabsContent>

        <TabsContent value="trace" className="pt-4">
          {simulation.trace_mermaid?.trim() && (
            <div className="rounded-md border p-3">
              {simulation.trace_mermaid.includes('classDef agent') && <TraceLegend />}
              <MermaidView source={simulation.trace_mermaid} />
            </div>
          )}
        </TabsContent>

        <TabsContent value="analysis" className="pt-4">
          {simulation.analysis && <MarkdownView content={simulation.analysis} />}
        </TabsContent>

        <TabsContent value="suggestions" className="space-y-3 pt-4">
          {simulation.suggestions.length === 0 ? (
            <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              No suggestions — the toolkit covered this scenario well.
            </p>
          ) : (
            simulation.suggestions.map((suggestion, index) => (
              <SuggestionCard
                key={index}
                simulation={simulation}
                suggestion={suggestion}
                index={index}
              />
            ))
          )}
        </TabsContent>

        <TabsContent value="scenario" className="pt-4">
          {hasScenario ? (
            <MarkdownView content={simulation.scenario} />
          ) : (
            <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              No scenario was provided for this run — Claude derived one silently from the goal, and
              it wasn't captured.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** Swatches matching the classDef colors the backend prompt pins for the trace. */
function TraceLegend() {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-4 border-b pb-2 text-xs text-muted-foreground">
      <LegendItem className="border-violet-600 bg-violet-100" label="Agent" />
      <LegendItem className="border-emerald-600 bg-emerald-100" label="Skill" />
      <LegendItem className="border-dashed border-red-600 bg-red-100" label="Gap / failure" />
      <LegendItem className="border-border bg-muted" label="Step" />
      <span className="inline-flex items-center gap-1.5">
        <span className="w-4 border-t border-dashed border-muted-foreground" aria-hidden />
        agent uses skill
      </span>
    </div>
  );
}

function LegendItem({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('size-3 rounded-sm border', className)} aria-hidden />
      {label}
    </span>
  );
}

function ScoreHeader({
  simulation,
  previous,
}: {
  simulation: Simulation;
  previous: Simulation | null;
}) {
  const score = simulation.score ?? 0;
  return (
    <section className="flex items-center gap-4 rounded-md border p-4">
      <span className={cn('text-4xl font-bold tabular-nums', scoreColor(score))}>{score}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Goal achievement score
          </p>
          {/* A control run graded a rubric it derived itself, so a delta against
              the previous run's rubric would compare two different questions. */}
          {simulation.control_run ? (
            <ControlRunBadge />
          ) : (
            <ScoreDelta current={simulation.score} previous={previous?.score ?? null} />
          )}
        </div>
        {simulation.verdict && <p className="mt-0.5 text-sm">{simulation.verdict}</p>}
      </div>
      <RunStats simulation={simulation} />
    </section>
  );
}

/** This run built its own checklist instead of inheriting the previous run's —
 * either asked for, or forced because that run scored 100 and its rubric was spent. */
function ControlRunBadge() {
  return (
    <span
      title="Fresh checklist: this run derived its own capability list instead of re-grading the previous run's, so the score isn't directly comparable. Forced automatically after a run scores 100."
      className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-medium text-sky-700 dark:text-sky-400"
    >
      <ClipboardCheck className="size-3" />
      fresh checklist
    </span>
  );
}

/** Change vs the previous completed run of the SAME scenario — the honest
 * "did it improve?" signal now that the score is a comparable coverage number. */
function ScoreDelta({ current, previous }: { current: number | null; previous: number | null }) {
  if (current == null || previous == null) return null;
  const delta = current - previous;
  if (delta === 0) {
    return (
      <span
        title="Same score as the previous run of this scenario"
        className="rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground"
      >
        ±0
      </span>
    );
  }
  const up = delta > 0;
  return (
    <span
      title={`${up ? 'Up' : 'Down'} ${Math.abs(delta)} vs the previous run of this scenario`}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium tabular-nums',
        up
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
          : 'bg-red-500/15 text-red-700 dark:text-red-400',
      )}
    >
      {up ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />}
      {up ? '+' : '−'}
      {Math.abs(delta)}
    </span>
  );
}

type ChecklistStatus = SimulationChecklistItem['status'];

const CHECKLIST_STATUS_ORDER: Record<ChecklistStatus, number> = {
  fail: 0,
  partial: 1,
  pass: 2,
  na: 3,
};

const CHECKLIST_STATUS_META: Record<
  ChecklistStatus,
  { label: string; Icon: typeof CheckCircle2; className: string }
> = {
  pass: { label: 'Pass', Icon: CheckCircle2, className: 'text-emerald-600 dark:text-emerald-400' },
  partial: {
    label: 'Partial',
    Icon: CircleDashed,
    className: 'text-amber-600 dark:text-amber-400',
  },
  fail: { label: 'Fail', Icon: XCircle, className: 'text-red-600 dark:text-red-400' },
  na: { label: 'N/A', Icon: MinusCircle, className: 'text-muted-foreground' },
};

const CHECKLIST_VALUE: Record<ChecklistStatus, number> = { pass: 1, partial: 0.5, fail: 0, na: 0 };

/** Renders the capability checklist the score is computed from: a coverage bar,
 * per-status counts, then the items with failing ones first. Items marked N/A
 * are excluded from the score (environmental / human-gated) and shown last. */
function ChecklistPanel({
  checklist,
  previous,
}: {
  checklist: SimulationChecklistItem[];
  previous: Simulation | null;
}) {
  const gradable = checklist.filter((item) => item.status !== 'na');
  const totalWeight = gradable.reduce((sum, item) => sum + item.weight, 0);
  const earned = gradable.reduce(
    (sum, item) => sum + item.weight * CHECKLIST_VALUE[item.status],
    0,
  );
  const coverage = totalWeight > 0 ? Math.round((100 * earned) / totalWeight) : null;

  const counts: Record<ChecklistStatus, number> = { pass: 0, partial: 0, fail: 0, na: 0 };
  for (const item of checklist) counts[item.status] += 1;

  // Map the previous run's items by id so each row can show what changed.
  const prevById = new Map((previous?.checklist ?? []).map((item) => [item.id, item.status]));

  const sorted = [...checklist].sort(
    (a, b) =>
      CHECKLIST_STATUS_ORDER[a.status] - CHECKLIST_STATUS_ORDER[b.status] || b.weight - a.weight,
  );

  return (
    <div className="space-y-4">
      <div className="rounded-md border p-4">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Capability coverage
          </p>
          {coverage != null && (
            <span className={cn('text-sm font-semibold tabular-nums', scoreColor(coverage))}>
              {coverage}%
            </span>
          )}
        </div>
        {coverage != null && (
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-emerald-500/70"
              style={{ width: `${coverage}%` }}
            />
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {(['pass', 'partial', 'fail', 'na'] as ChecklistStatus[])
            .filter((status) => counts[status] > 0)
            .map((status) => {
              const { label, Icon, className } = CHECKLIST_STATUS_META[status];
              return (
                <span key={status} className="inline-flex items-center gap-1.5">
                  <Icon className={cn('size-3.5', className)} />
                  {counts[status]} {label}
                  {status === 'na' && ' (excluded)'}
                </span>
              );
            })}
        </div>
      </div>

      <ul className="space-y-1.5">
        {sorted.map((item) => (
          <ChecklistRow key={item.id} item={item} previousStatus={prevById.get(item.id) ?? null} />
        ))}
      </ul>
    </div>
  );
}

function ChecklistRow({
  item,
  previousStatus,
}: {
  item: SimulationChecklistItem;
  previousStatus: ChecklistStatus | null;
}) {
  const { Icon, className, label } = CHECKLIST_STATUS_META[item.status];
  // Only surface a transition when the item actually moved since last run.
  const improved =
    previousStatus != null &&
    previousStatus !== item.status &&
    CHECKLIST_VALUE[item.status] > CHECKLIST_VALUE[previousStatus];
  const regressed =
    previousStatus != null &&
    previousStatus !== item.status &&
    CHECKLIST_VALUE[item.status] < CHECKLIST_VALUE[previousStatus];

  return (
    <li className="flex gap-3 rounded-md border bg-muted/20 px-3 py-2">
      <Icon className={cn('mt-0.5 size-4 shrink-0', className)} aria-label={label} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{item.title}</span>
          <span
            title={`Weight ${item.weight} of 3`}
            className="rounded bg-muted px-1 text-[10px] font-medium tabular-nums text-muted-foreground"
          >
            w{item.weight}
          </span>
          {improved && (
            <span className="rounded-full bg-emerald-500/15 px-1.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
              ↑ {CHECKLIST_STATUS_META[previousStatus!].label} → {label}
            </span>
          )}
          {regressed && (
            <span className="rounded-full bg-red-500/15 px-1.5 text-[10px] font-medium text-red-700 dark:text-red-400">
              ↓ {CHECKLIST_STATUS_META[previousStatus!].label} → {label}
            </span>
          )}
        </div>
        {item.evidence.trim() && (
          <p className="mt-0.5 text-xs text-muted-foreground">{item.evidence}</p>
        )}
      </div>
    </li>
  );
}

/** Right-hand column of the score card: how the run went, per the CLI's own
 * report. Old runs (no stats) still get a duration from the row timestamps. */
function RunStats({ simulation }: { simulation: Simulation }) {
  const stats = simulation.stats;
  const durationMs =
    stats?.duration_ms ??
    (simulation.completed_at
      ? new Date(simulation.completed_at).getTime() - new Date(simulation.created_at).getTime()
      : null);
  const tokensIn =
    (stats?.input_tokens ?? 0) +
    (stats?.cache_read_tokens ?? 0) +
    (stats?.cache_creation_tokens ?? 0);

  const rows: { label: string; value: string; title?: string }[] = [];
  if (durationMs != null && durationMs > 0) {
    rows.push({ label: 'Duration', value: formatDuration(durationMs) });
  }
  if (stats?.model) rows.push({ label: 'Model', value: shortModel(stats.model) });
  if (stats?.output_tokens != null) {
    rows.push({
      label: 'Tokens',
      value: `${formatTokens(tokensIn)} in · ${formatTokens(stats.output_tokens)} out`,
      title: `input ${stats.input_tokens ?? 0} · cache read ${stats.cache_read_tokens ?? 0} · cache write ${stats.cache_creation_tokens ?? 0} · output ${stats.output_tokens}`,
    });
  }
  if (stats?.cost_usd != null) {
    rows.push({
      label: 'Cost',
      value: `$${stats.cost_usd.toFixed(stats.cost_usd < 0.1 ? 3 : 2)}`,
      title: 'As reported by the claude CLI (covered by your subscription)',
    });
  }
  if (rows.length === 0) return null;

  return (
    <dl className="shrink-0 space-y-0.5 border-l pl-4 text-xs">
      {rows.map((row) => (
        <div key={row.label} className="flex justify-between gap-3" title={row.title}>
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="font-medium tabular-nums">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function formatTokens(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

/** "claude-opus-4-6" → "opus-4-6"; strips a trailing -YYYYMMDD date too. */
function shortModel(model: string): string {
  return model.replace(/^claude-/, '').replace(/-\d{8}$/, '');
}

const IMPACT_STYLES: Record<SimulationSuggestion['impact'], string> = {
  high: 'border-transparent bg-red-500/15 text-red-700 dark:text-red-400',
  medium: 'border-transparent bg-amber-500/15 text-amber-700 dark:text-amber-400',
  low: 'border-transparent bg-muted text-muted-foreground',
};

function SuggestionCard({
  simulation,
  suggestion,
  index,
}: {
  simulation: Simulation;
  suggestion: SimulationSuggestion;
  index: number;
}) {
  const [{ mutateAsync: apply, isPending }] = useAtom(applySuggestionMutationAtom);
  const queryClient = useQueryClient();

  async function handleApply() {
    try {
      const updated = await apply({ simulationId: simulation.id, index });
      // Swap the updated run into the cached list without a refetch.
      queryClient.setQueryData<Simulation[]>(['simulations', simulation.project_id], (old) =>
        old?.map((s) => (s.id === updated.id ? updated : s)),
      );
      const result = updated.suggestions[index];
      if (result?.status === 'applied') {
        queryClient.invalidateQueries({ queryKey: ['assets'] });
        for (const change of suggestion.changes) {
          if (change.asset_id) {
            queryClient.invalidateQueries({ queryKey: ['asset', change.asset_id] });
          }
        }
        // The backend auto-syncs project links for applied changes.
        queryClient.invalidateQueries({ queryKey: ['project', simulation.project_id] });
        queryClient.invalidateQueries({ queryKey: ['projects'] });
        toast.success('Suggestion applied', {
          description: 'Files written and project links updated.',
        });
      } else {
        toast.error('Apply failed', { description: result?.error ?? 'Unknown error' });
      }
    } catch (err) {
      toast.error('Apply failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={IMPACT_STYLES[suggestion.impact]}>{suggestion.impact} impact</Badge>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{suggestion.title}</span>
        <SuggestionAction suggestion={suggestion} applying={isPending} onApply={handleApply} />
      </div>

      {suggestion.rationale.trim() && (
        <MarkdownView content={suggestion.rationale} className="text-sm" />
      )}

      {suggestion.status === 'failed' && suggestion.error && (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {suggestion.error}
        </p>
      )}

      {suggestion.changes.length > 0 && (
        <ul className="space-y-2">
          {suggestion.changes.map((change, changeIndex) => (
            <li key={changeIndex} className="rounded-md border bg-muted/30 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Badge variant="outline" className="uppercase">
                  {change.action}
                </Badge>
                <code className="min-w-0 flex-1 truncate font-mono">{shortPath(change.path)}</code>
              </div>
              {change.description && (
                <p className="mt-1 text-xs text-muted-foreground">{change.description}</p>
              )}
              {change.new_content != null && (
                <details className="mt-2">
                  <summary className="flex cursor-pointer select-none items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                    <FileCode2 className="size-3.5" /> View new file content
                  </summary>
                  <pre className="mt-2 max-h-80 overflow-auto rounded bg-muted px-3 py-2 font-mono text-xs">
                    {change.new_content}
                  </pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SuggestionAction({
  suggestion,
  applying,
  onApply,
}: {
  suggestion: SimulationSuggestion;
  applying: boolean;
  onApply: () => void;
}) {
  if (suggestion.status === 'applied') {
    return (
      <Badge variant="success">
        <Check className="mr-1 size-3" />
        Applied{suggestion.applied_at ? ` ${absoluteDate(suggestion.applied_at)}` : ''}
      </Badge>
    );
  }
  if (suggestion.changes.length === 0) {
    return <Badge variant="muted">advice only</Badge>;
  }
  return (
    <Button size="sm" onClick={onApply} disabled={applying}>
      {applying ? <Loader2 className="animate-spin" /> : <Wand2 />}
      {suggestion.status === 'failed' ? 'Retry apply' : 'Apply'}
    </Button>
  );
}

/** Shorten an absolute path under $HOME to ~/… for display. */
function shortPath(path: string): string {
  const match = path.match(/^\/Users\/[^/]+\/(.+)$/);
  return match ? `~/${match[1]}` : path;
}
