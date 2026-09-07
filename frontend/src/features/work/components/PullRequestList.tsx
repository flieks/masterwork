import { useState } from 'react';
import { useAtom } from 'jotai';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitPullRequest,
  RefreshCw,
  Wrench,
} from 'lucide-react';
import type { WorkPullRequest, WorkSource } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { EmptyState } from '~/components/EmptyState';
import { FolderPickerDialog } from '~/components/FolderPickerDialog';
import { apiErrorMessage } from '~/api/client';
import { sessionDetailPath } from '~/features/sessions';
import {
  delegatePullRequestMutationAtom,
  isHttpUrl,
  prBranchLabel,
  prThreadsQueryAtom,
  pullRequestsQueryAtom,
  saveRepoPathMutationAtom,
  sourceLabel,
  syncPullRequestsMutationAtom,
  unresolvedThreadCount,
} from '../queries';
import { PullRequestThreads } from './PullRequestThreads';

interface PullRequestListProps {
  sources: WorkSource[];
}

/** What "Fix comments" surfaces when the PR's repository has no known checkout. */
interface UnresolvedRepo {
  prId: number;
  remoteUrl: string;
  reason: string;
}

export function PullRequestList({ sources }: PullRequestListProps) {
  const [prs] = useAtom(pullRequestsQueryAtom);
  const [{ mutateAsync: sync, isPending: syncing, variables: syncingSourceId }] =
    useAtom(syncPullRequestsMutationAtom);
  const [{ mutateAsync: delegate, isPending: delegating, variables: delegatingPrId }] =
    useAtom(delegatePullRequestMutationAtom);
  const [{ mutateAsync: saveRepoPath, isPending: savingPath }] =
    useAtom(saveRepoPathMutationAtom);
  const navigate = useNavigate();

  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Once a row has been opened, its unresolved badge keeps showing even after
  // it is collapsed again — the threads query itself stays cached.
  const [everOpened, setEverOpened] = useState<Set<number>>(new Set());
  const [unresolvedRepo, setUnresolvedRepo] = useState<UnresolvedRepo | null>(null);

  function toggle(prId: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(prId)) next.delete(prId);
      else next.add(prId);
      return next;
    });
    setEverOpened((current) => (current.has(prId) ? current : new Set(current).add(prId)));
  }

  async function runSync(source: WorkSource) {
    try {
      const result = await sync(source.id);
      toast.success(`Synced pull requests for ${sourceLabel(source)}`, {
        description: `${result.fetched} fetched · ${result.inserted} new · ${result.updated} updated`,
      });
    } catch (err) {
      toast.error('Could not sync pull requests', { description: apiErrorMessage(err) });
    }
  }

  async function fixComments(pr: WorkPullRequest) {
    try {
      const response = await delegate(pr.id);
      if (response.resolved && response.run_id) {
        toast.success(`Fixing PR #${pr.external_id}`, {
          description: 'A factory run was launched from the assembled prompt.',
          action: {
            label: 'View run',
            onClick: () => navigate(sessionDetailPath(`factory-${response.run_id}`)),
          },
        });
      } else if (!response.resolved) {
        setUnresolvedRepo({
          prId: pr.id,
          remoteUrl: response.remote_url,
          reason:
            response.reason ?? 'This repository could not be resolved to a local checkout.',
        });
      }
    } catch (err) {
      toast.error('Could not delegate this PR', { description: apiErrorMessage(err) });
    }
  }

  async function confirmFolder(path: string) {
    if (!unresolvedRepo) return;
    const target = unresolvedRepo;
    try {
      await saveRepoPath({ remote_url: target.remoteUrl, local_path: path });
      setUnresolvedRepo(null);
      // The user should only ever have to pick a folder once per remote —
      // retry the same delegate automatically now that it is saved.
      const pr = (prs.data ?? []).find((p) => p.id === target.prId);
      if (pr) await fixComments(pr);
    } catch (err) {
      toast.error('Could not save that folder', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {sources.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {sources.map((source) => {
            const isSyncingThis = syncing && syncingSourceId === source.id;
            return (
              <Button
                key={source.id}
                variant="outline"
                size="sm"
                disabled={syncing}
                onClick={() => void runSync(source)}
              >
                <RefreshCw className={isSyncingThis ? 'animate-spin' : undefined} />
                {isSyncingThis ? 'Syncing…' : `Sync ${sourceLabel(source)}`}
              </Button>
            );
          })}
        </div>
      ) : null}

      {prs.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : prs.isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load pull requests"
          description={apiErrorMessage(prs.error)}
          action={
            <Button variant="outline" size="sm" onClick={() => void prs.refetch()}>
              Retry
            </Button>
          }
        />
      ) : prs.data.length === 0 ? (
        <EmptyState
          icon={<GitPullRequest className="size-8" />}
          title="No pull requests yet"
          description="Sync a source above to pull its open PRs in."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {prs.data.map((pr) => (
            <li key={pr.id} className="rounded-lg border bg-card">
              <div className="flex flex-wrap items-center gap-3 p-3">
                <button
                  type="button"
                  aria-expanded={expanded.has(pr.id)}
                  aria-label={`${expanded.has(pr.id) ? 'Collapse' : 'Expand'} #${pr.external_id}`}
                  onClick={() => toggle(pr.id)}
                  className="shrink-0 rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {expanded.has(pr.id) ? (
                    <ChevronDown className="size-4" />
                  ) : (
                    <ChevronRight className="size-4" />
                  )}
                </button>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-xs text-muted-foreground">
                      #{pr.external_id}
                    </span>
                    {/* PR title comes from DevOps — plain text, never rendered as markup. */}
                    <span className="truncate font-medium" title={pr.title}>
                      {pr.title}
                    </span>
                    {pr.is_draft ? <Badge variant="muted">Draft</Badge> : null}
                    {isHttpUrl(pr.external_url) ? (
                      <a
                        href={pr.external_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-muted-foreground hover:text-foreground"
                        aria-label={`Open PR #${pr.external_id} in Azure DevOps`}
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    ) : null}
                  </div>
                  <p className="truncate text-xs text-muted-foreground">
                    {pr.repository_name} · {prBranchLabel(pr)}
                    {pr.created_by ? ` · ${pr.created_by}` : ''}
                  </p>
                </div>

                {everOpened.has(pr.id) ? <UnresolvedCountBadge prId={pr.id} /> : null}

                <Button
                  variant="outline"
                  size="sm"
                  disabled={delegating}
                  onClick={() => void fixComments(pr)}
                >
                  <Wrench className="size-4" />
                  {delegating && delegatingPrId === pr.id ? 'Delegating…' : 'Fix comments'}
                </Button>
              </div>

              {expanded.has(pr.id) ? (
                <div className="border-t px-3 py-2">
                  <PullRequestThreads prId={pr.id} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <FolderPickerDialog
        open={unresolvedRepo !== null}
        onOpenChange={(open) => !open && setUnresolvedRepo(null)}
        title="Choose the checkout for this repository"
        description={unresolvedRepo?.reason ?? ''}
        confirmLabel="Use this folder"
        isConfirming={savingPath || delegating}
        onConfirm={(path) => void confirmFolder(path)}
      />
    </div>
  );
}

/** Populated lazily: threads are only fetched once a row has been expanded,
 * never eagerly for every row on load. */
function UnresolvedCountBadge({ prId }: { prId: number }) {
  const [{ data }] = useAtom(prThreadsQueryAtom(prId));
  if (!data) return null;
  const count = unresolvedThreadCount(data);
  return (
    <Badge variant={count > 0 ? 'secondary' : 'muted'} aria-label={`${count} unresolved comments`}>
      {count} unresolved
    </Badge>
  );
}
