import { useAtom, type PrimitiveAtom } from 'jotai';
import { GitBranch, ScanSearch } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { cn } from '~/lib/utils';
import {
  analyticsIncludeChildrenAtom,
  analyticsIncludeInspectionAtom,
  analyticsWindowAtom,
  analyticsWorkflowAtom,
} from '../queries';
import type { AnalyticsWindow } from '../stats';

/**
 * One filter bar for all four aggregates.
 *
 * The API takes the same four parameters on every endpoint so the numbers share
 * a population; putting them in four places on screen would let a reader
 * compare a gate table from last week against a role table from today.
 */

const WINDOWS: { value: AnalyticsWindow; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'all', label: 'All time' },
];

const WORKFLOWS: { value: string | null; label: string }[] = [
  { value: null, label: 'All' },
  { value: 'factory', label: 'Factory' },
  { value: 'chat', label: 'Chat' },
];

const INSPECTION_HINT =
  "Masterwork analyses an asset by running Claude over it, and those runs Read every linked asset's SKILL.md. Counting them measures masterwork rather than the work — and because the same exclusion applies to all four aggregates, getting it wrong does not make one number wrong, it makes every number wrong together.";

const CHILDREN_HINT =
  "A pipeline's headless stage child is the inside view of a stage already counted on its parent: the stage's cost, verdict and corrections are reported there. Counting both puts the same work in twice and adds a main role that did the pipeline's work a second time.";

export function AnalyticsFilters() {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented atom={analyticsWindowAtom} label="Since" options={WINDOWS} />
        <Segmented atom={analyticsWorkflowAtom} label="Workflow" options={WORKFLOWS} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <PopulationToggle
          atom={analyticsIncludeInspectionAtom}
          hint={INSPECTION_HINT}
          onLabel="Hide inspection runs"
          offLabel="Include inspection runs"
          icon={<ScanSearch className="size-4" />}
        />
        <PopulationToggle
          atom={analyticsIncludeChildrenAtom}
          hint={CHILDREN_HINT}
          onLabel="Fold child runs back in"
          offLabel="Count child runs separately"
          icon={<GitBranch className="size-4" />}
        />
      </div>
    </div>
  );
}

/** Same idiom as the run and asset filters — the backend does the narrowing. */
function Segmented<T extends string | null>({
  atom,
  label,
  options,
}: {
  atom: PrimitiveAtom<T>;
  label: string;
  options: { value: T; label: string }[];
}) {
  const [selected, setSelected] = useAtom(atom);

  return (
    <div className="inline-flex items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <div className="inline-flex rounded-md border p-0.5" role="group" aria-label={label}>
        {options.map((option) => {
          const active = selected === option.value;
          return (
            <button
              key={option.label}
              type="button"
              aria-pressed={active}
              onClick={() => setSelected(option.value)}
              className={cn(
                'rounded-sm px-2 py-1 text-xs transition-colors',
                active
                  ? 'bg-secondary font-medium text-secondary-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** A switch that changes who is counted, worded so the default is legible. */
function PopulationToggle({
  atom,
  hint,
  onLabel,
  offLabel,
  icon,
}: {
  atom: PrimitiveAtom<boolean>;
  hint: string;
  onLabel: string;
  offLabel: string;
  icon: React.ReactNode;
}) {
  const [on, setOn] = useAtom(atom);

  return (
    <Button
      variant={on ? 'secondary' : 'outline'}
      size="sm"
      aria-pressed={on}
      onClick={() => setOn(!on)}
      title={hint}
    >
      {icon}
      {on ? onLabel : offLabel}
    </Button>
  );
}
