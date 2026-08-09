import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronRight, History } from 'lucide-react';
import type { AssetCall, AssetSessionUse } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { apiErrorMessage } from '~/api/client';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
// The leaf, not the feature index — that one pulls the Sessions pages in.
import { sessionDetailPath } from '~/features/sessions/runs';
import { assetSessionUsesQueryAtom } from '../queries';
import { callSourceLabel, noInputReason, orderedInput } from '../usage';

/**
 * Which runs reached for this asset, and what each call handed it.
 *
 * Two levels of collapse, and both earn it: the panel keeps the asset's own
 * markdown at the top of the page, and a row expands before it navigates
 * because the arguments are the reason to open a run at all.
 *
 * The count rides the header, so the panel answers "has anything used this?"
 * without being opened — which is why the query runs on mount rather than on
 * the first click, the same as the chat panel above it.
 */
export function AssetUsageLog({ assetId }: { assetId: string }) {
  const [open, setOpen] = useState(false);
  const [{ data, isPending, isError, error, refetch }] = useAtom(
    assetSessionUsesQueryAtom(assetId),
  );
  const runs = data?.length ?? 0;

  return (
    <section className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium hover:bg-accent/40"
      >
        <History className="size-4 text-muted-foreground" aria-hidden="true" />
        Used by
        {isPending ? null : (
          <span className="text-xs font-normal text-muted-foreground">
            {runs === 0 ? 'no runs yet' : `${runs} ${runs === 1 ? 'run' : 'runs'}`}
          </span>
        )}
        <ChevronDown
          className={cn(
            'ml-auto size-4 text-muted-foreground transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>

      {open ? (
        <div className="border-t p-3">
          {isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : isError ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <AlertTriangle className="size-4" /> {apiErrorMessage(error)}
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                Retry
              </Button>
            </p>
          ) : data.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No recorded run has used this yet. Uses are counted from Claude Code hook events, so
              only runs since the hooks were installed appear here.
            </p>
          ) : (
            <div className="overflow-hidden rounded-lg border">
              <table className="w-full text-sm">
                <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">Session</th>
                    <th className="px-3 py-2 text-right font-medium">Uses</th>
                    <th className="px-3 py-2 font-medium">Last used</th>
                    <th className="w-px px-3 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.map((row) => (
                    <SessionRow key={row.session_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

function SessionRow({ row }: { row: AssetSessionUse }) {
  const [open, setOpen] = useState(false);
  const title = row.title?.trim() || row.git_repo || row.session_id;

  return (
    <>
      <tr className="transition-colors hover:bg-accent/40">
        <td className="px-3 py-2">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="flex w-full items-start gap-1.5 text-left"
          >
            <ChevronRight
              className={cn(
                'mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform',
                open && 'rotate-90',
              )}
              aria-hidden="true"
            />
            <span className="min-w-0">
              <span className="line-clamp-2 font-medium">{title}</span>
              {row.git_repo ? (
                <span className="text-xs text-muted-foreground">{row.git_repo}</span>
              ) : null}
            </span>
          </button>
        </td>
        <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{row.uses}</td>
        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
          <time dateTime={row.last_used_at} title={absoluteDateTime(row.last_used_at)}>
            {relativeTime(row.last_used_at)}
          </time>
        </td>
        <td className="px-3 py-2">
          <Link
            to={sessionDetailPath(row.session_id)}
            className="whitespace-nowrap text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            Open run
          </Link>
        </td>
      </tr>
      {open ? (
        <tr className="bg-muted/20">
          <td colSpan={4} className="px-3 py-2">
            <CallList calls={row.calls} uses={row.uses} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function CallList({ calls, uses }: { calls: AssetCall[]; uses: number }) {
  if (calls.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        This run was recorded before the per-call log shipped. Rebuild it from its stored events to
        see the arguments.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {calls.map((call, i) => (
        <Call key={`${call.used_at}-${i}`} call={call} />
      ))}
      {calls.length < uses ? (
        <p className="text-xs text-muted-foreground">
          Showing {calls.length} of {uses} calls.
        </p>
      ) : null}
    </div>
  );
}

function Call({ call }: { call: AssetCall }) {
  const input = orderedInput(call);
  return (
    <div className="rounded-md border bg-background px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="rounded border px-1.5 py-0.5 font-medium text-foreground">
          {callSourceLabel(call.source)}
        </span>
        {call.lane ? <span className="font-mono">{call.lane}</span> : null}
        <time dateTime={call.used_at} title={absoluteDateTime(call.used_at)}>
          {relativeTime(call.used_at)}
        </time>
      </div>
      {input.length === 0 ? (
        <p className="mt-1.5 text-xs italic text-muted-foreground">{noInputReason(call.source)}</p>
      ) : (
        <dl className="mt-1.5 space-y-1.5">
          {input.map(([key, value]) => (
            <div key={key} className="space-y-0.5">
              <dt className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
                {key}
              </dt>
              <dd className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1 font-mono text-xs">
                {value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
