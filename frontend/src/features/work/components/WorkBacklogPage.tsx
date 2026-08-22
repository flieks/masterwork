import { useMemo, useState } from 'react';
import { useAtom } from 'jotai';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Inbox, SearchX } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { toast } from '~/components/ui/sonner';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import {
  ASSIGNEE_ME,
  assigneeOptions,
  buildWorkItemTree,
  countNodes,
  filterWorkItemTree,
  hasActiveFilters,
  pullRequestsQueryAtom,
  sprintOptions,
  startWorkItemMutationAtom,
  workFilterSelectionAtom,
  workItemsQueryAtom,
  workSourcesQueryAtom,
  type OwnerNames,
  type WorkItemFilters,
} from '../queries';
import { NewWorkSourceForm } from './NewWorkSourceForm';
import { PullRequestList } from './PullRequestList';
import { StartPromptDialog, type StartedItem } from './StartPromptDialog';
import { WorkFilters } from './WorkFilters';
import { WorkItemDetailDialog } from './WorkItemDetailDialog';
import { WorkItemTable } from './WorkItemTable';
import { WorkSourceBar } from './WorkSourceBar';

const NO_FILTERS: WorkItemFilters = { iteration: null, assignee: null, query: '' };

const VIEWS = ['backlog', 'prs'] as const;
type WorkView = (typeof VIEWS)[number];

function isWorkView(value: string | null): value is WorkView {
  return VIEWS.includes(value as WorkView);
}

export function WorkBacklogPage() {
  const [params, setParams] = useSearchParams();
  const view: WorkView = isWorkView(params.get('view')) ? (params.get('view') as WorkView) : 'backlog';

  const [sources] = useAtom(workSourcesQueryAtom);
  const [items] = useAtom(workItemsQueryAtom);
  const [prs] = useAtom(pullRequestsQueryAtom);
  const [{ mutateAsync: start, isPending: starting, variables: startingId }] =
    useAtom(startWorkItemMutationAtom);

  const [selection, setSelection] = useAtom(workFilterSelectionAtom);
  const [query, setQuery] = useState('');
  const [started, setStarted] = useState<StartedItem | null>(null);
  const [detail, setDetail] = useState<WorkItem | null>(null);

  const loaded = useMemo(() => items.data ?? [], [items.data]);
  const sprints = useMemo(() => sprintOptions(loaded), [loaded]);
  const assignees = useMemo(() => assigneeOptions(loaded), [loaded]);
  // The team's current sprint, provided the loaded items mention it at all.
  const activeSprint =
    sources.data?.map((s) => s.current_iteration).find((ci) => ci && sprints.includes(ci)) ??
    null;
  // Until the user touches the filters, the view opens on the active sprint.
  // The picked sprint + assignee persist across reloads; a stored value that no
  // longer exists (ended sprint, vanished name) falls back to the default.
  const filters = useMemo<WorkItemFilters>(() => {
    const iteration =
      selection === null || (selection.iteration !== null && !sprints.includes(selection.iteration))
        ? activeSprint
        : selection.iteration;
    const assignee =
      selection !== null &&
      (selection.assignee === null ||
        selection.assignee === ASSIGNEE_ME ||
        assignees.includes(selection.assignee))
        ? selection.assignee
        : null;
    return { iteration, assignee, query };
  }, [selection, activeSprint, sprints, assignees, query]);

  function changeFilters(next: WorkItemFilters) {
    setQuery(next.query);
    setSelection({ iteration: next.iteration, assignee: next.assignee });
  }
  const tree = useMemo(() => buildWorkItemTree(loaded), [loaded]);
  const owners = useMemo<OwnerNames>(
    () => Object.fromEntries((sources.data ?? []).map((s) => [s.id, s.owner_display_name])),
    [sources.data],
  );
  const visible = useMemo(
    () => filterWorkItemTree(tree, filters, owners),
    [tree, filters, owners],
  );
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
  const shownCount = view === 'prs' ? (prs.data?.length ?? null) : countNodes(visible);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Work</h1>
          {hasSources && shownCount !== null ? (
            <Badge
              variant="muted"
              aria-label={`${shownCount} ${view === 'prs' ? 'pull requests' : 'work items'}`}
            >
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

          <Tabs
            value={view}
            onValueChange={(next) =>
              setParams(next === 'backlog' ? {} : { view: next }, { replace: true })
            }
            className="flex flex-col gap-4"
          >
            <TabsList className="self-start">
              <TabsTrigger value="backlog">Backlog</TabsTrigger>
              <TabsTrigger value="prs">Pull requests</TabsTrigger>
            </TabsList>

            <TabsContent value="backlog" className="flex flex-col gap-4">
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
                  <WorkFilters
                    filters={filters}
                    onChange={changeFilters}
                    sprints={sprints}
                    activeSprint={activeSprint}
                    assignees={assignees}
                  />
                  {visible.length === 0 ? (
                    <EmptyState
                      icon={<SearchX className="size-8" />}
                      title="No work item matches these filters"
                      action={
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => changeFilters(NO_FILTERS)}
                        >
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
            </TabsContent>

            <TabsContent value="prs">
              <PullRequestList sources={sources.data} />
            </TabsContent>
          </Tabs>
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
