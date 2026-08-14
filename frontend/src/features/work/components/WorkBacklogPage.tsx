import { useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, Inbox } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { startWorkItemMutationAtom, workItemsQueryAtom, workSourcesQueryAtom } from '../queries';
import { NewWorkSourceForm } from './NewWorkSourceForm';
import { StartPromptDialog, type StartedItem } from './StartPromptDialog';
import { WorkItemTable } from './WorkItemTable';
import { WorkSourceBar } from './WorkSourceBar';

export function WorkBacklogPage() {
  const [sources] = useAtom(workSourcesQueryAtom);
  const [items] = useAtom(workItemsQueryAtom);
  const [{ mutateAsync: start, isPending: starting, variables: startingId }] =
    useAtom(startWorkItemMutationAtom);
  const [started, setStarted] = useState<StartedItem | null>(null);

  async function startItem(item: WorkItem) {
    try {
      setStarted({ item, response: await start(item.id) });
    } catch (err) {
      toast.error('Could not assemble the session prompt', { description: apiErrorMessage(err) });
    }
  }

  const hasSources = !sources.isPending && !sources.isError && sources.data.length > 0;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">Work</h1>
          {hasSources && items.data ? (
            <Badge variant="muted" aria-label={`${items.data.length} work items`}>
              {items.data.length}
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
          ) : items.data.length === 0 ? (
            <EmptyState
              icon={<Inbox className="size-8" />}
              title="No work items yet"
              description="Sync a source above to pull its backlog in."
            />
          ) : (
            <WorkItemTable
              items={items.data}
              onStart={(item) => void startItem(item)}
              startingId={starting ? (startingId ?? null) : null}
            />
          )}
        </>
      )}

      <StartPromptDialog started={started} onClose={() => setStarted(null)} />
    </div>
  );
}
