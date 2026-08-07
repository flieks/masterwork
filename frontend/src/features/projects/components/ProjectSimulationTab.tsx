import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import {
  ClipboardCheck,
  ClipboardList,
  FlaskConical,
  Loader2,
  Play,
  Repeat,
  Sparkles,
  Square,
  Trash2,
  TriangleAlert,
} from 'lucide-react';
import type { Project, Simulation, SimulationChange } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { Textarea } from '~/components/ui/textarea';
import { EmptyState } from '~/components/EmptyState';
import { toast } from '~/components/ui/sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import {
  createSimulationMutationAtom,
  deleteSimulationMutationAtom,
  generateScenarioMutationAtom,
  simulationsQueryAtom,
  startAutopilotMutationAtom,
  stopAutopilotMutationAtom,
} from '../simulationQueries';
import { CrossChangeAlert } from './CrossChangeAlert';
import { SimulationDetail, scoreColor } from './SimulationDetail';

export function ProjectSimulationTab({ project }: { project: Project }) {
  const [{ data: simulations, isPending }] = useAtom(simulationsQueryAtom(project.id));
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = simulations ?? [];
  const selected = list.find((s) => s.id === selectedId) ?? list[0] ?? null;
  const running = list.find((s) => s.status === 'running') ?? null;
  // The prior completed run of the SAME scenario — powers the score delta and
  // per-item "what changed" badges in the detail pane.
  const selectedIndex = selected ? list.findIndex((s) => s.id === selected.id) : -1;
  const previous =
    selected && selectedIndex >= 0
      ? (list
          .slice(selectedIndex + 1)
          .find(
            (s) => s.status === 'completed' && s.score != null && s.scenario === selected.scenario,
          ) ?? null)
      : null;

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-1/4 min-w-72 max-w-96 flex-col border-r">
        <div className="space-y-3 border-b p-4">
          <CrossChangeAlert projectId={project.id} />
          <RunPanel project={project} running={running} onStarted={setSelectedId} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {isPending || list.length === 0 ? (
            <p className="px-1 py-2 text-xs text-muted-foreground">
              {isPending ? 'Loading runs…' : 'No runs yet.'}
            </p>
          ) : (
            <RunList list={list} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
          )}
        </div>
      </aside>

      <section className="min-h-0 flex-1 overflow-y-auto">
        {selected ? (
          <div className="mx-auto w-full max-w-4xl p-6">
            <SimulationDetail simulation={selected} previous={previous} />
          </div>
        ) : isPending ? null : (
          <EmptyState
            className="mt-16"
            icon={<FlaskConical className="size-8" />}
            title="No simulations yet"
            description="Run one to see whether your linked skills and agents actually achieve the project goal."
          />
        )}
      </section>
    </div>
  );
}

function RunPanel({
  project,
  running,
  onStarted,
}: {
  project: Project;
  running: Simulation | null;
  onStarted: (id: string) => void;
}) {
  const [{ mutateAsync: create, isPending }] = useAtom(createSimulationMutationAtom);
  const [{ mutateAsync: generate, isPending: generating }] = useAtom(generateScenarioMutationAtom);
  const [{ mutateAsync: startAutopilot, isPending: startingAutopilot }] = useAtom(
    startAutopilotMutationAtom,
  );
  const [{ mutateAsync: stopAutopilot, isPending: stopping }] = useAtom(stopAutopilotMutationAtom);
  const queryClient = useQueryClient();
  const [scenario, setScenario] = useState(project.scenario);
  const [controlRun, setControlRun] = useState(false);
  const [autopilotOpen, setAutopilotOpen] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);

  const hasRunning = running !== null;
  const autopilotRunId = running?.autopilot_run_id ?? null;
  const runningScenario = running?.scenario ?? null;

  // Autopilot rotates in a fresh scenario after a run scores 100 — follow it, so
  // the box shows what is actually being simulated. Safe to overwrite: the
  // textarea is disabled while a run is in flight.
  useEffect(() => {
    if (autopilotRunId && runningScenario !== null) setScenario(runningScenario);
  }, [autopilotRunId, runningScenario]);

  async function run() {
    try {
      const simulation = await create({
        projectId: project.id,
        body: { scenario, control_run: controlRun },
      });
      queryClient.invalidateQueries({ queryKey: ['simulations', project.id] });
      // Backend mirrors the submitted scenario onto the project.
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
      onStarted(simulation.id);
      toast.success('Simulation started', {
        description: 'Claude is reading the linked assets and walking the scenario.',
      });
    } catch (err) {
      toast.error('Could not start the simulation', { description: apiErrorMessage(err) });
    }
  }

  async function runAutopilot(iterations: number) {
    try {
      const simulation = await startAutopilot({
        projectId: project.id,
        body: { scenario, iterations, control_run: controlRun },
      });
      setAutopilotOpen(false);
      setStopRequested(false);
      queryClient.invalidateQueries({ queryKey: ['simulations', project.id] });
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
      onStarted(simulation.id);
      toast.success('Autopilot started', {
        description: `Up to ${iterations} runs; suggestions are applied automatically between runs.`,
      });
    } catch (err) {
      toast.error('Could not start the autopilot', { description: apiErrorMessage(err) });
    }
  }

  async function requestStop() {
    if (!autopilotRunId) return;
    try {
      await stopAutopilot(autopilotRunId);
      setStopRequested(true);
      toast.success('Autopilot stopping', {
        description: 'It finishes the current run, then stops. Its suggestions stay pending.',
      });
    } catch (err) {
      toast.error('Could not stop the autopilot', { description: apiErrorMessage(err) });
    }
  }

  async function generateScenario() {
    try {
      const { scenario: draft } = await generate(project.id);
      setScenario(draft);
      // Backend persisted the draft on the project.
      queryClient.invalidateQueries({ queryKey: ['project', project.id] });
    } catch (err) {
      toast.error('Could not generate a scenario', { description: apiErrorMessage(err) });
    }
  }

  const busy = isPending || hasRunning || generating || startingAutopilot;
  // Mirrors the backend guard: a run with no linked assets grades an undefined toolkit.
  const noAssets = project.asset_ids.length === 0;
  return (
    <div className="space-y-3">
      {noAssets && (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          <TriangleAlert className="mr-1.5 inline size-3.5 text-amber-500" />
          No assets linked — a simulation needs a toolkit to test.{' '}
          <Link to="?tab=overview" className="font-medium text-foreground underline">
            Link assets in Overview
          </Link>{' '}
          — the Suggest button there picks a starter set for your goal.
        </p>
      )}
      <div>
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Goal</h2>
        <p
          className="mt-1 line-clamp-3 whitespace-pre-wrap text-sm text-muted-foreground"
          title={project.goal}
        >
          {project.goal || 'No goal set — add one in the Overview tab.'}
        </p>
      </div>
      <div>
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Scenario
        </h2>
        <Textarea
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          aria-label="Scenario to simulate"
          placeholder="Optional — write your own, press Generate to draft one, or leave empty to let Claude derive one silently."
          className="mt-1 min-h-20 text-sm"
          disabled={busy || noAssets}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={run} disabled={busy || noAssets}>
          {isPending || hasRunning ? <Loader2 className="animate-spin" /> : <Play />}
          {hasRunning ? 'Running…' : isPending ? 'Starting…' : 'Run simulation'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setAutopilotOpen(true)}
          disabled={busy || noAssets}
        >
          <Repeat />
          Autopilot
        </Button>
        <Button size="sm" variant="outline" onClick={generateScenario} disabled={busy || noAssets}>
          {generating ? <Loader2 className="animate-spin" /> : <Sparkles />}
          {generating ? 'Generating…' : scenario.trim() ? 'Regenerate scenario' : 'Generate scenario'}
        </Button>
        <Button
          size="sm"
          variant={controlRun ? 'secondary' : 'outline'}
          aria-pressed={controlRun}
          onClick={() => setControlRun((on) => !on)}
          disabled={busy || noAssets}
          className={cn(controlRun && 'ring-1 ring-primary/40')}
          title="Build the capability checklist from scratch instead of re-grading the previous run's. Forced automatically after a run scores 100."
        >
          {controlRun ? <ClipboardCheck /> : <ClipboardList />}
          Fresh checklist
        </Button>
      </div>
      {controlRun && (
        <p className="text-xs text-muted-foreground">
          The next run derives its checklist from scratch. Its score isn't comparable to the
          previous run's — a different rubric is being graded.
        </p>
      )}
      {hasRunning && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {autopilotRunId
              ? `Autopilot run ${running.autopilot_iteration}/${running.autopilot_total} — suggestions are applied automatically between runs.`
              : 'This usually takes a few minutes — results appear here automatically.'}
          </p>
          {autopilotRunId && (
            <Button
              size="sm"
              variant="outline"
              onClick={requestStop}
              disabled={stopping || stopRequested}
            >
              <Square />
              {stopRequested ? 'Stopping after this run…' : 'Stop autopilot'}
            </Button>
          )}
        </div>
      )}
      <AutopilotDialog
        open={autopilotOpen}
        onOpenChange={setAutopilotOpen}
        starting={startingAutopilot}
        onStart={runAutopilot}
      />
    </div>
  );
}

function AutopilotDialog({
  open,
  onOpenChange,
  starting,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  starting: boolean;
  onStart: (iterations: number) => void;
}) {
  const [iterations, setIterations] = useState('5');
  const parsed = Number.parseInt(iterations, 10);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 20;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run autopilot</DialogTitle>
          <DialogDescription>
            Runs the simulation back-to-back, automatically applying every suggestion between runs.
            It stops early when a run yields no suggestions — unless that run scored 100, in which
            case a fresh scenario is generated and the chain continues. The last run's suggestions
            are left pending for you to review.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) onStart(parsed);
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="autopilot-iterations" className="text-sm font-medium">
              Maximum runs
            </label>
            <Input
              id="autopilot-iterations"
              type="number"
              min={1}
              max={20}
              value={iterations}
              onChange={(e) => setIterations(e.target.value)}
              autoFocus
            />
            <p className="text-xs text-muted-foreground">
              1–20. Each run takes a few minutes, so 5 runs can take a while.
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || starting}>
              {starting ? <Loader2 className="animate-spin" /> : <Repeat />}
              {starting ? 'Starting…' : 'Start autopilot'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RunList({
  list,
  selectedId,
  onSelect,
}: {
  list: Simulation[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <section className="space-y-2">
      <h2 className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Runs
      </h2>
      <ul className="space-y-1.5">
        {list.map((simulation) => (
          <RunRow
            key={simulation.id}
            simulation={simulation}
            selected={simulation.id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </section>
  );
}

function RunRow({
  simulation,
  selected,
  onSelect,
}: {
  simulation: Simulation;
  selected: boolean;
  onSelect: (id: string | null) => void;
}) {
  const [{ mutateAsync: remove, isPending: deleting }] = useAtom(deleteSimulationMutationAtom);
  const queryClient = useQueryClient();

  async function handleDelete() {
    try {
      await remove(simulation.id);
      if (selected) onSelect(null);
      queryClient.invalidateQueries({ queryKey: ['simulations', simulation.project_id] });
      toast.success('Simulation deleted');
    } catch (err) {
      toast.error('Delete failed', { description: apiErrorMessage(err) });
    }
  }

  const label =
    simulation.scenario.trim() ||
    (simulation.status === 'running'
      ? 'Deriving scenario from the goal…'
      : 'Auto-derived scenario');

  return (
    <li
      className={cn(
        'group rounded-md border px-3 py-2 transition-colors',
        selected ? 'border-primary/50 bg-accent/50' : 'hover:bg-accent/30',
      )}
    >
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onSelect(simulation.id)}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <ScoreDot simulation={simulation} />
          <span className="min-w-0 flex-1 truncate text-sm">{label}</span>
          {simulation.control_run && (
            <span
              title="Fresh checklist — this run derived its own capability list instead of re-grading the previous run's."
              className="shrink-0 rounded-full bg-sky-500/15 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 dark:text-sky-400"
            >
              fresh
            </span>
          )}
          {simulation.autopilot_iteration != null && (
            <span
              title={`Autopilot run ${simulation.autopilot_iteration} of ${simulation.autopilot_total}`}
              className="shrink-0 rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 dark:text-violet-400"
            >
              auto {simulation.autopilot_iteration}/{simulation.autopilot_total}
            </span>
          )}
          <span
            className="shrink-0 text-xs text-muted-foreground"
            title={absoluteDateTime(simulation.created_at)}
          >
            {relativeTime(simulation.created_at)}
          </span>
        </button>
        <button
          type="button"
          aria-label="Delete simulation"
          onClick={handleDelete}
          disabled={deleting || simulation.status === 'running'}
          className="rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-destructive group-hover:opacity-100 disabled:invisible"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
      <ChangeStats simulation={simulation} />
    </li>
  );
}

function ScoreDot({ simulation }: { simulation: Simulation }) {
  if (simulation.status === 'running') {
    return <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />;
  }
  if (simulation.status === 'failed' || simulation.score == null) {
    return <TriangleAlert className="size-4 shrink-0 text-destructive" />;
  }
  return (
    <span
      className={cn(
        'w-8 shrink-0 text-sm font-semibold tabular-nums',
        scoreColor(simulation.score),
      )}
    >
      {simulation.score}
    </span>
  );
}

type ChangeAction = SimulationChange['action'];

const CHANGE_ORDER: ChangeAction[] = ['create', 'update', 'delete', 'link', 'unlink'];

const CHANGE_STYLES: Record<ChangeAction, string> = {
  create: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  update: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  delete: 'bg-red-500/15 text-red-700 dark:text-red-400',
  link: 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
  unlink: 'bg-zinc-500/15 text-zinc-700 dark:text-zinc-400',
};

const CHANGE_LABELS: Record<ChangeAction, string> = {
  create: 'created',
  update: 'updated',
  delete: 'deleted',
  link: 'linked',
  unlink: 'unlinked',
};

/** Per-action asset names touched by this run's APPLIED suggestions. */
function appliedChangeNames(simulation: Simulation): Record<ChangeAction, string[]> {
  const names: Record<ChangeAction, string[]> = {
    create: [],
    update: [],
    delete: [],
    link: [],
    unlink: [],
  };
  for (const suggestion of simulation.suggestions) {
    if (suggestion.status !== 'applied') continue;
    for (const change of suggestion.changes) {
      const name = changeName(change);
      if (!names[change.action].includes(name)) names[change.action].push(name);
    }
  }
  return names;
}

/** Human name of a changed asset: skills by folder name, agents by file stem. */
function changeName(change: SimulationChange): string {
  const parts = change.path.split('/').filter(Boolean);
  const file = parts.at(-1) ?? change.path;
  if (file.toUpperCase() === 'SKILL.MD' && parts.length >= 2) return parts.at(-2)!;
  return file.replace(/\.md$/i, '');
}

/** Applied-change pills (e.g. "2 updated"); hover lists the asset names. Zero counts are hidden. */
function ChangeStats({ simulation }: { simulation: Simulation }) {
  const names = appliedChangeNames(simulation);
  const actions = CHANGE_ORDER.filter((action) => names[action].length > 0);
  if (actions.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1 pl-11">
      {actions.map((action) => (
        <span
          key={action}
          title={names[action].join(', ')}
          className={cn(
            'cursor-default rounded-full px-2 py-0.5 text-[11px] font-medium',
            CHANGE_STYLES[action],
          )}
        >
          {names[action].length} {CHANGE_LABELS[action]}
        </span>
      ))}
    </div>
  );
}
