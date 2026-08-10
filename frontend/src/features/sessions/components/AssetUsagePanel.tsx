import { useAtom, type PrimitiveAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { AlertTriangle, Bot, Boxes, HelpCircle, ScanSearch, Sparkles } from 'lucide-react';
import type { CodingAssetUsage } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { relativeTime, absoluteDateTime } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { assetUsePath, isUnresolvedAsset, UNRESOLVED_HINT, usesBarPct } from '../assets';
import {
  assetKindFilterAtom,
  assetWindowAtom,
  codingAssetUsageQueryAtom,
  includeInspectionAtom,
  type AssetWindow,
} from '../queries';

/**
 * The flywheel view: every skill and agent, ranked by how much work it has
 * actually done across every recorded run. This is what tells you which assets
 * earn their keep and which are dead weight.
 */
export function AssetUsagePanel() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(codingAssetUsageQueryAtom);
  const [includeInspection] = useAtom(includeInspectionAtom);

  return (
    <div className="flex flex-col gap-3">
      <AssetFilters />
      {includeInspection ? <InspectionNote /> : null}
      {isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load asset usage"
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : data.length === 0 ? (
        <EmptyState
          icon={<Sparkles className="size-8" />}
          title="No asset usage recorded"
          description="Skills and agents are counted as runs use them. Nothing matched this window and kind."
        />
      ) : (
        <UsageTable rows={data} />
      )}
    </div>
  );
}

const WINDOWS: { value: AssetWindow; label: string }[] = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: 'all', label: 'All time' },
];

const KINDS: { value: string | null; label: string }[] = [
  { value: null, label: 'All' },
  { value: 'skill', label: 'Skills' },
  { value: 'agent', label: 'Agents' },
];

const INSPECTION_HINT =
  "Masterwork analyses an asset by running Claude over it, and those runs Read every linked asset's SKILL.md. Counting them ranks assets by how often masterwork inspected them rather than by the work they did, so they are left out unless you ask.";

function AssetFilters() {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented atom={assetKindFilterAtom} label="Kind" options={KINDS} />
        <Segmented atom={assetWindowAtom} label="Used" options={WINDOWS} />
      </div>
      <InspectionToggle />
    </div>
  );
}

/**
 * The rollup's honesty valve. Excluding masterwork's own analysis runs is the
 * default because including them measures masterwork, not the assets — but
 * there was no way to look at all, so the exclusion was invisible.
 */
function InspectionToggle() {
  const [includeInspection, setIncludeInspection] = useAtom(includeInspectionAtom);

  return (
    <Button
      variant={includeInspection ? 'secondary' : 'outline'}
      size="sm"
      aria-pressed={includeInspection}
      onClick={() => setIncludeInspection(!includeInspection)}
      title={INSPECTION_HINT}
    >
      <ScanSearch className="size-4" />
      {includeInspection ? 'Hide inspection runs' : 'Include inspection runs'}
    </Button>
  );
}

/** Shown only while counting them, because then the ranking needs the caveat. */
function InspectionNote() {
  return (
    <p className="text-xs text-muted-foreground">
      Counting masterwork&rsquo;s own analysis runs, which Read every linked asset&rsquo;s SKILL.md
      — these numbers rank assets by inspection as much as by use.
    </p>
  );
}

/** Same idiom as the run filters — the backend does the narrowing. */
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

function UsageTable({ rows }: { rows: CodingAssetUsage[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th scope="col" className="px-3 py-2 font-medium">
              Asset
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Kind
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Runs
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Uses
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Last used
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row) => (
            <UsageRow key={`${row.kind}:${row.name}`} row={row} rows={rows} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsageRow({ row, rows }: { row: CodingAssetUsage; rows: CodingAssetUsage[] }) {
  const unresolved = isUnresolvedAsset(row);
  const href = assetUsePath(row);
  const Icon = unresolved ? HelpCircle : row.kind === 'agent' ? Bot : Boxes;

  return (
    <tr
      className={cn('transition-colors hover:bg-accent/40', unresolved && 'text-muted-foreground')}
    >
      <td className="px-3 py-2">
        <span className="flex items-center gap-1.5">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          {href ? (
            <Link to={href} className="font-medium hover:underline">
              {row.name}
            </Link>
          ) : (
            <span className="font-medium">{row.name}</span>
          )}
          {unresolved ? (
            <span
              title={UNRESOLVED_HINT}
              className="rounded border border-dashed px-1 text-[10px] uppercase tracking-wide"
            >
              unresolved
            </span>
          ) : null}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">{row.kind}</td>
      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{row.sessions}</td>
      <td className="px-3 py-2">
        <span className="flex items-center gap-2">
          <span className="w-10 shrink-0 font-mono text-xs tabular-nums">{row.uses}</span>
          {/*
            Scaled to the busiest resolved asset, not to the unresolved bucket —
            that one bucket holds an order of magnitude more uses than any real
            name and would flatten every skill into an invisible sliver.
          */}
          <span className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className={cn(
                'block h-full rounded-full',
                unresolved ? 'bg-muted-foreground/40' : 'bg-primary/60',
              )}
              style={{ width: `${usesBarPct(row, rows)}%` }}
            />
          </span>
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        <time dateTime={row.last_used_at} title={absoluteDateTime(row.last_used_at)}>
          {relativeTime(row.last_used_at)}
        </time>
      </td>
    </tr>
  );
}
