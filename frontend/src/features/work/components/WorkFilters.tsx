import { Search } from 'lucide-react';
import { Input } from '~/components/ui/input';
import { sprintLabel, type WorkItemFilters } from '../queries';

interface WorkFiltersProps {
  filters: WorkItemFilters;
  onChange: (filters: WorkItemFilters) => void;
  /** Every sprint the loaded items mention, as full iteration paths. */
  sprints: string[];
}

export function WorkFilters({ filters, onChange, sprints }: WorkFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Sprint"
        value={filters.iteration ?? ''}
        title={filters.iteration ?? 'All sprints'}
        onChange={(e) => onChange({ ...filters, iteration: e.target.value || null })}
        className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
      >
        <option value="">All sprints</option>
        {sprints.map((sprint) => (
          // Trimmed for reading; the value filtered on is the full path.
          <option key={sprint} value={sprint} title={sprint}>
            {sprintLabel(sprint)}
          </option>
        ))}
      </select>

      <div className="relative min-w-0 flex-1 sm:max-w-xs">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search titles"
          placeholder="Search titles…"
          value={filters.query}
          onChange={(e) => onChange({ ...filters, query: e.target.value })}
          className="pl-8"
        />
      </div>
    </div>
  );
}
