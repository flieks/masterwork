import { useMemo, useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, Inbox, SearchX } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import {
  buildWorkItemTree,
  countNodes,
  filterWorkItemTree,
  hasActiveFilters,
  sprintOptions,
  startWorkItemMutationAtom,
  workItemsQueryAtom,
  workSourcesQueryAtom,
  type WorkItemFilters,
} from '../queries';
import { NewWorkSourceForm } from './NewWorkSourceForm';
import { StartPromptDialog, type StartedItem } from './StartPromptDialog';
import { WorkFilters } from './WorkFilters';
import { WorkItemDetailDialog } from './WorkItemDetailDialog';
import { WorkItemTable } from './WorkItemTable';
import { WorkSourceBar } from './WorkSourceBar';

const NO_FILTERS: WorkItemFilters = { iteration: null, query: '' };

export function WorkBacklogPage() {
  const [sources] = useAtom(workSourcesQueryAtom);
  const [items] = useAtom(workItemsQueryAtom);
  const [{ mutateAsync: start, isPending: starting, variables: startingId }] =
    useAtom(startWorkItemMutationAtom);

  const [filters, setFilters] = useState<WorkItemFilters>(NO_FILTERS);
  const [started, setStarted] = useState<StartedItem | null>(null);
  const [detail, setDetail] = useState<WorkItem | null>(null);

  const loaded = useMemo(() => items.data ?? [], [items.data]);
  const sprints = useMemo(() => sprintOptions(loaded), [loaded]);
  const tree = useMemo(() => buildWorkItemTree(loaded), [loaded]);
  const visible = useMemo(() => filterWorkItemTree(tree, filters), [tree, filters]);
  const filtered = hasActiveFilters(filters);

  async function startItem(item: WorkItem) {
    // One modal at a time: the prompt replaces the detail it was started from.
    setDetail(null);
    try {
      setStarted({ item, response: await start(item.id) });
    } catch (err) {
      toast.error('Could not assemble the session prompt', { description: apiErrorMessage(err) });
    }
  }

  const hasSources = !sources.isPending && !sources.isError && sources.data.length > 0;
  const shownCount = countNodes(visible);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Work</h1>
          {hasSources && items.data ? (
            <Badge variant="muted" aria-label={`${shownCount} work items`}>
              {shownCount}
            </Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          The Azure DevOps backlog, pulled in read-only. Sync a source to refresh it; starting an
          item assembles the session prompt for it.
        </p>
      </header>

      {sources.isPending ? (
        <Skeleton className="h-20 w-full" />
      ) : sources.isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load work sources"
          description={apiErrorMessage(sources.error)}
          action={
            <Button variant="outline" size="sm" onClick={() => sources.refetch()}>
              Retry
            </Button>
          }
        />
      ) : sources.data.length === 0 ? (
        <NewWorkSourceForm />
      ) : (
        <>
          <WorkSourceBar sources={sources.data} />

          {items.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : items.isError ? (
            <EmptyState
              icon={<AlertTriangle className="size-8" />}
              title="Couldn't load work items"
              description={apiErrorMessage(items.error)}
              action={
                <Button variant="outline" size="sm" onClick={() => items.refetch()}>
                  Retry
                </Button>
              }
            />
          ) : loaded.length === 0 ? (
            <EmptyState
              icon={<Inbox className="size-8" />}
              title="No work items yet"
              description="Sync a source above to pull its backlog in."
            />
          ) : (
            <>
              <WorkFilters filters={filters} onChange={setFilters} sprints={sprints} />
              {visible.length === 0 ? (
                <EmptyState
                  icon={<SearchX className="size-8" />}
                  title="No work item matches these filters"
                  action={
                    <Button variant="outline" size="sm" onClick={() => setFilters(NO_FILTERS)}>
                      Clear filters
                    </Button>
                  }
                />
              ) : (
                <WorkItemTable
                  // Turning filtering on or off resets which groups are open,
                  // so a manual collapse never survives into the next mode.
                  key={filtered ? 'filtered' : 'all'}
                  nodes={visible}
                  onStart={(item) => void startItem(item)}
                  onOpen={setDetail}
                  startingId={starting ? (startingId ?? null) : null}
                  forceExpanded={filtered}
                />
              )}
            </>
          )}
        </>
      )}

      <WorkItemDetailDialog
        item={detail}
        onClose={() => setDetail(null)}
        onStart={(item) => void startItem(item)}
        starting={starting}
      />
      <StartPromptDialog started={started} onClose={() => setStarted(null)} />
    </div>
  );
}
