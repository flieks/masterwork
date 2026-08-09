import { useAtom, type PrimitiveAtom } from 'jotai';
import { cn } from '~/lib/utils';
import { statusFilterAtom, workflowFilterAtom } from '../queries';

interface FilterOption {
  value: string | null;
  label: string;
}

const WORKFLOW_OPTIONS: FilterOption[] = [
  { value: null, label: 'All' },
  { value: 'factory', label: 'Factory' },
  { value: 'chat', label: 'Chat' },
];

const STATUS_OPTIONS: FilterOption[] = [
  { value: null, label: 'All' },
  { value: 'running', label: 'Running' },
  { value: 'success', label: 'Success' },
  { value: 'failed', label: 'Failed' },
  { value: 'interrupted', label: 'Interrupted' },
  // Derived, not stored: most runs never report an outcome, so this is the
  // largest bucket and the one you filter to when auditing what got lost.
  { value: 'abandoned', label: 'Abandoned' },
];

/** Workflow + status, straight onto the list query's params. */
export function RunFilters() {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <SegmentedFilter atom={workflowFilterAtom} label="Workflow" options={WORKFLOW_OPTIONS} />
      <SegmentedFilter atom={statusFilterAtom} label="Status" options={STATUS_OPTIONS} />
    </div>
  );
}

/** The backend does the filtering — these only decide what the query asks for. */
function SegmentedFilter({
  atom,
  label,
  options,
}: {
  atom: PrimitiveAtom<string | null>;
  label: string;
  options: FilterOption[];
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
