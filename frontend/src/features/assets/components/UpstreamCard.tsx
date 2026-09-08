import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { DownloadCloud, ExternalLink, RefreshCw } from 'lucide-react';
import type { DriftStatus, InstalledSkill, UpstreamCheckResult } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { UnifiedDiffView } from '~/components/UnifiedDiffView';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '~/components/ui/alert-dialog';
import { Badge, type BadgeProps } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { absoluteDate, relativeTime } from '~/lib/datetime';
import {
  checkUpstreamMutationAtom,
  installedSkillsQueryAtom,
  updateFromUpstreamMutationAtom,
} from '../queries';

const STATUS_BADGE: Record<DriftStatus, { label: string; variant: BadgeProps['variant'] }> = {
  current: { label: 'Up to date', variant: 'success' },
  edited_locally: { label: 'Edited locally', variant: 'outline' },
  upstream_changed: { label: 'Update available', variant: 'default' },
  diverged: { label: 'Diverged', variant: 'destructive' },
  unknown_origin: { label: 'Source gone', variant: 'muted' },
};

const STATUS_TEXT: Record<DriftStatus, string> = {
  current: 'Your copy matches its source.',
  edited_locally: 'You edited this copy; the source has not changed since you installed it.',
  upstream_changed: 'The source has changed since you installed it.',
  diverged: 'Both sides changed: you edited this copy, and the source moved on.',
  unknown_origin: 'The source folder no longer exists upstream.',
};

/** Overwriting these statuses throws local edits away — they need a confirm. */
const LOSES_LOCAL_EDITS = new Set<DriftStatus>(['edited_locally', 'diverged']);
const UPDATABLE = new Set<DriftStatus>(['upstream_changed', 'edited_locally', 'diverged']);

export function DriftBadge({ status }: { status: DriftStatus | null | undefined }) {
  if (!status) return <Badge variant="muted">Not checked yet</Badge>;
  const { label, variant } = STATUS_BADGE[status];
  return <Badge variant={variant}>{label}</Badge>;
}

/** Where a catalog-installed skill came from, and whether either side has
 *  drifted since — checked on request, never on page load, since every check
 *  spends GitHub quota. Renders nothing for a skill masterwork did not install. */
export function UpstreamCard({ name }: { name: string }) {
  const [{ data: installed }] = useAtom(installedSkillsQueryAtom);
  const row = installed?.find((s) => s.name === name);
  if (!row) return null;
  return <InstalledUpstreamCard row={row} />;
}

function InstalledUpstreamCard({ row }: { row: InstalledSkill }) {
  const [{ mutateAsync: check, isPending: checking }] = useAtom(checkUpstreamMutationAtom);
  const [{ mutateAsync: update, isPending: updating }] = useAtom(updateFromUpstreamMutationAtom);
  // Kept per card, not on the shared mutation atom, so one skill's result never
  // shows on another's page.
  const [result, setResult] = useState<UpstreamCheckResult | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    setResult(null);
  }, [row.name]);

  async function runCheck() {
    try {
      setResult(await check(row.name));
    } catch (err) {
      toast.error("Couldn't check the source", { description: apiErrorMessage(err) });
    }
  }

  async function runUpdate(force: boolean) {
    try {
      await update({ name: row.name, force });
      setConfirming(false);
      setResult(null);
      toast.success(`Updated ${row.name} from source`);
    } catch (err) {
      setConfirming(false);
      toast.error("Couldn't update from source", { description: apiErrorMessage(err) });
    }
  }

  function handleUpdateClick() {
    if (result && LOSES_LOCAL_EDITS.has(result.status)) {
      setConfirming(true);
      return;
    }
    void runUpdate(false);
  }

  const status = result?.status ?? row.drift_status ?? null;

  return (
    <section aria-label="Upstream" className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <p className="text-sm font-medium">Upstream</p>
          <a
            href={row.source_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            {row.owner}/{row.repo}
            <ExternalLink className="size-3" />
          </a>
          <DriftBadge status={status} />
          {!result && row.last_checked_at ? (
            <span className="text-xs text-muted-foreground">
              checked {relativeTime(row.last_checked_at)}
            </span>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2">
          {result && UPDATABLE.has(result.status) ? (
            <Button size="sm" onClick={handleUpdateClick} disabled={updating || checking}>
              <DownloadCloud /> {updating ? 'Updating…' : 'Update from source'}
            </Button>
          ) : null}
          <Button size="sm" variant="outline" onClick={runCheck} disabled={checking || updating}>
            <RefreshCw className={checking ? 'animate-spin' : undefined} />{' '}
            {checking ? 'Checking…' : 'Check upstream'}
          </Button>
        </div>
      </div>

      {result ? (
        <div className="space-y-3 px-3 py-2.5">
          <p className="text-sm">{STATUS_TEXT[result.status]}</p>
          {result.upstream_last_modified_at || result.upstream_last_change_summary ? (
            <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              {result.upstream_last_modified_at ? (
                <div className="flex gap-1.5">
                  <dt>Source last changed</dt>
                  <dd className="text-foreground">
                    {absoluteDate(result.upstream_last_modified_at)}{' '}
                    <span className="text-muted-foreground">
                      ({relativeTime(result.upstream_last_modified_at)})
                    </span>
                  </dd>
                </div>
              ) : null}
              {/* Commit subject from the source repo — plain text only. */}
              {result.upstream_last_change_summary ? (
                <div className="flex min-w-0 gap-1.5">
                  <dt className="shrink-0">Latest commit</dt>
                  <dd className="min-w-0 truncate text-foreground">
                    {result.upstream_last_change_summary}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
          {result.skill_md_diff ? (
            <UnifiedDiffView diff={result.skill_md_diff} label="SKILL.md diff" />
          ) : result.status !== 'unknown_origin' ? (
            <p className="text-xs text-muted-foreground">SKILL.md is identical on both sides.</p>
          ) : null}
          {result.other_changes.length > 0 ? (
            <div>
              <p className="text-xs font-medium text-muted-foreground">Other files</p>
              <ul className="mt-1 space-y-0.5">
                {result.other_changes.map((change) => (
                  <li key={change.path} className="flex items-center gap-2 text-xs">
                    <Badge variant="muted" className="uppercase">
                      {change.change}
                    </Badge>
                    <code className="font-mono">{change.path}</code>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      <AlertDialog
        open={confirming}
        onOpenChange={(next) => (!next && !updating ? setConfirming(false) : undefined)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Overwrite your local edits to “{row.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              You changed this skill after installing it. Updating replaces the whole folder with
              the copy at {row.owner}/{row.repo}, and your local edits are lost — the previous
              version stays in the folder’s git snapshot only if you made one.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={updating}>Keep my edits</AlertDialogCancel>
            <AlertDialogAction
              // Radix closes on click; the dialog must outlive the request.
              onClick={(e) => {
                e.preventDefault();
                void runUpdate(true);
              }}
              disabled={updating}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {updating ? 'Updating…' : 'Overwrite and update'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
