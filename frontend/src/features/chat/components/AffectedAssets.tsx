import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Boxes, Bot, Check } from 'lucide-react';
import type { ChatMessage } from '~/api/generated';
import { assetDetailPath, parseAssetId } from '~/features/assets';

interface AffectedAsset {
  id: string;
  applied: boolean;
}

/** Distinct assets referenced by the session's proposals; `applied` when any applied. */
function affectedAssetsFor(messages: ChatMessage[]): AffectedAsset[] {
  const map = new Map<string, boolean>();
  for (const message of messages) {
    const proposal = message.proposal;
    if (!proposal) continue;
    for (const change of proposal.changes) {
      if (!change.asset_id) continue;
      map.set(change.asset_id, (map.get(change.asset_id) ?? false) || proposal.status === 'applied');
    }
  }
  return [...map.entries()].map(([id, applied]) => ({ id, applied }));
}

/**
 * Pill strip above the composer linking to every asset this chat's proposals
 * touch, so applied changes are one click away from verification.
 */
export function AffectedAssets({ messages }: { messages: ChatMessage[] }) {
  const assets = useMemo(() => affectedAssetsFor(messages), [messages]);
  if (assets.length === 0) return null;

  return (
    <section aria-label="Assets in this chat" className="border-t bg-muted/30 px-4 py-2">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Affected assets
        </span>
        {assets.map(({ id, applied }) => {
          const parsed = parseAssetId(id);
          if (!parsed) return null;
          const Icon = parsed.kind === 'skill' ? Boxes : Bot;
          return (
            <Link
              key={id}
              to={assetDetailPath(parsed.kind, parsed.name, parsed.provider)}
              title={
                applied
                  ? `${id} — has applied changes; open to verify`
                  : `${id} — referenced by proposals in this chat`
              }
              className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-0.5 text-xs transition-colors hover:border-ring hover:bg-accent"
            >
              <Icon className="size-3 text-muted-foreground" />
              {parsed.name}
              {applied ? <Check aria-label="applied" className="size-3 text-emerald-500" /> : null}
            </Link>
          );
        })}
      </div>
    </section>
  );
}
