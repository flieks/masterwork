import { useMemo, useState } from 'react';
import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Boxes, Bot, Pencil, HelpCircle } from 'lucide-react';
import type { AssetSummary, Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import {
  allAssetsQueryAtom,
  assetDetailPath,
  parseAssetId,
  type AssetKind,
} from '~/features/assets';
import { useCrossChangedAssetIds } from './CrossChangeAlert';
import { EditLinksDialog } from './EditLinksDialog';

const GROUP_META: Record<AssetKind, { label: string; icon: typeof Boxes }> = {
  skill: { label: 'Skills', icon: Boxes },
  agent: { label: 'Agents', icon: Bot },
};

export function LinkedAssets({ project }: { project: Project }) {
  const [{ data: allAssets }] = useAtom(allAssetsQueryAtom);
  const [editing, setEditing] = useState(false);
  const changedIds = useCrossChangedAssetIds(project.id);

  const byId = useMemo(() => {
    const map = new Map<string, AssetSummary>();
    for (const a of allAssets ?? []) map.set(a.id, a);
    return map;
  }, [allAssets]);

  const groups = useMemo(() => {
    const skills: string[] = [];
    const agents: string[] = [];
    const unknown: string[] = [];
    for (const id of project.asset_ids) {
      const known = byId.get(id);
      const kind = known?.kind ?? parseAssetId(id)?.kind;
      if (kind === 'skill') skills.push(id);
      else if (kind === 'agent') agents.push(id);
      else unknown.push(id);
    }
    return { skills, agents, unknown };
  }, [project.asset_ids, byId]);

  const empty = project.asset_ids.length === 0;

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Linked assets</h2>
        <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
          <Pencil /> Edit links
        </Button>
      </div>

      {empty ? (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No assets linked yet. Ask the project chat to analyze your scenario and propose skills + a
          flow diagram, or link them manually with “Edit links”.
        </p>
      ) : (
        <div className="space-y-4">
          <AssetGroup kind="skill" ids={groups.skills} byId={byId} changedIds={changedIds} />
          <AssetGroup kind="agent" ids={groups.agents} byId={byId} changedIds={changedIds} />
          {groups.unknown.length > 0 ? <UnknownGroup ids={groups.unknown} /> : null}
        </div>
      )}

      <EditLinksDialog
        project={project}
        open={editing}
        onOpenChange={setEditing}
        allAssets={allAssets ?? []}
      />
    </section>
  );
}

function AssetGroup({
  kind,
  ids,
  byId,
  changedIds,
}: {
  kind: AssetKind;
  ids: string[];
  byId: Map<string, AssetSummary>;
  changedIds: Set<string>;
}) {
  if (ids.length === 0) return null;
  const { label, icon: Icon } = GROUP_META[kind];

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => {
          const asset = byId.get(id);
          if (!asset) return null;
          const changed = changedIds.has(id);
          return (
            <Link
              key={id}
              to={assetDetailPath(asset.kind, asset.name, asset.provider)}
              title={
                changed
                  ? `${asset.title} — changed by another project since this project's last run`
                  : asset.description || asset.title
              }
              className="inline-flex items-center gap-1.5 rounded-md border bg-card px-2.5 py-1 text-sm transition-colors hover:border-ring hover:bg-accent"
            >
              {changed ? <span className="size-1.5 rounded-full bg-amber-500" /> : null}
              {asset.title}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function UnknownGroup({ ids }: { ids: string[] }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <HelpCircle className="size-3.5" />
        Not installed
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => {
          const parsed = parseAssetId(id);
          return (
            <span
              key={id}
              title={`${id} — not installed under ~/.claude`}
              className="inline-flex items-center rounded-md border border-dashed px-2.5 py-1 font-mono text-xs text-muted-foreground"
            >
              {parsed?.name ?? id}
            </span>
          );
        })}
      </div>
    </div>
  );
}
