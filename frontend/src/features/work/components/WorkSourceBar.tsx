import { useAtom } from 'jotai';
import { RefreshCw } from 'lucide-react';
import type { WorkSource } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { sourceLabel, syncWorkSourceMutationAtom } from '../queries';

interface WorkSourceBarProps {
  sources: WorkSource[];
}

export function WorkSourceBar({ sources }: WorkSourceBarProps) {
  const [{ mutateAsync: sync, isPending, variables: syncingId }] = useAtom(
    syncWorkSourceMutationAtom,
  );

  async function runSync(source: WorkSource) {
    try {
      const result = await sync(source.id);
      toast.success(`Synced ${sourceLabel(source)}`, {
        description: `${result.fetched} fetched · ${result.inserted} new · ${result.updated} updated`,
      });
    } catch (err) {
      toast.error('Could not sync the source', { description: apiErrorMessage(err) });
    }
  }

  return (
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {sources.map((source) => {
        const syncing = isPending && syncingId === source.id;
        return (
          <li
            key={source.id}
            className="flex items-center justify-between gap-3 rounded-lg border bg-card p-3"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{sourceLabel(source)}</p>
              <p className="truncate text-xs text-muted-foreground">
                {source.team ? `${source.team} · ` : ''}
                {source.last_sync_at ? (
                  <>
                    synced{' '}
                    <time
                      dateTime={source.last_sync_at}
                      title={absoluteDateTime(source.last_sync_at)}
                    >
                      {relativeTime(source.last_sync_at)}
                    </time>
                  </>
                ) : (
                  'never synced'
                )}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={isPending}
              aria-label={`Sync ${sourceLabel(source)}`}
              onClick={() => void runSync(source)}
            >
              <RefreshCw className={syncing ? 'animate-spin' : undefined} />
              {syncing ? 'Syncing…' : 'Sync'}
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
