import { Link } from 'react-router-dom';
import { Bot, Boxes, HelpCircle } from 'lucide-react';
import type { AssetUse } from '~/api/generated';
import { cn } from '~/lib/utils';
import { assetUsePath, isUnresolvedAsset, UNRESOLVED_HINT } from '../assets';

const KIND_ICON = { skill: Boxes, agent: Bot } as const;

const BASE =
  'inline-flex max-w-full items-center gap-1 rounded-md border bg-card px-1.5 py-0.5 text-xs';

/**
 * One skill or agent a run used, linking to its masterwork page — the whole
 * point of recording them is being able to go and improve the thing.
 *
 * `asLink` is off on cards: the card is itself an anchor, and an anchor inside
 * an anchor is invalid HTML that browsers silently unnest.
 */
export function AssetChip({
  asset,
  asLink = true,
  className,
}: {
  asset: AssetUse;
  asLink?: boolean;
  className?: string;
}) {
  const unresolved = isUnresolvedAsset(asset);
  const Icon = unresolved ? HelpCircle : (KIND_ICON[asset.kind as 'skill' | 'agent'] ?? Boxes);
  const href = asLink ? assetUsePath(asset) : null;

  const body = (
    <>
      <Icon className="size-3 shrink-0" aria-hidden="true" />
      <span className="truncate">{asset.name}</span>
      {asset.uses > 1 ? (
        <span className="shrink-0 font-mono text-[10px] opacity-70">×{asset.uses}</span>
      ) : null}
    </>
  );

  if (unresolved) {
    return (
      <span
        title={UNRESOLVED_HINT}
        className={cn(BASE, 'border-dashed text-muted-foreground', className)}
      >
        {body}
      </span>
    );
  }

  const title = `${asset.kind} ${asset.name} — used ${asset.uses}×`;
  if (!href) {
    return (
      <span title={title} className={cn(BASE, className)}>
        {body}
      </span>
    );
  }

  return (
    <Link
      to={href}
      title={title}
      className={cn(BASE, 'transition-colors hover:border-ring hover:bg-accent', className)}
    >
      {body}
    </Link>
  );
}
