import { useState, type MouseEvent, type KeyboardEvent } from 'react';
import { useAtom } from 'jotai';
import { GitMerge } from 'lucide-react';
import type { AssetSummary } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { migrateAssetMutationAtom } from '../queries';
import { MakeGenericDialog } from './MakeGenericDialog';

type Twin = NonNullable<AssetSummary['generic_twin']>;

/** This agent-folder skill is a real copy of one also sitting in ~/.agents/skills. */
export function GenericTwinBadge({ twin }: { twin: Twin }) {
  if (twin === 'identical') {
    return (
      <Badge
        variant="outline"
        className="whitespace-nowrap text-muted-foreground"
        title="~/.agents/skills holds a byte-identical copy; this folder is a leftover from a per-agent install"
      >
        Duplicate of generic copy
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="whitespace-nowrap border-amber-500/40 text-amber-700 dark:text-amber-400"
      title="~/.agents/skills holds a different copy of this skill"
    >
      Differs from generic copy
    </Badge>
  );
}

function swallow(e: MouseEvent | KeyboardEvent) {
  e.preventDefault();
  e.stopPropagation();
}

/**
 * One click folds the pair into a single generic skill: an identical twin is
 * adopted outright, a differing one asks before the generic copy gives way.
 */
export function MergeTwinButton({ asset }: { asset: AssetSummary }) {
  const twin = asset.generic_twin;
  const [{ mutateAsync: migrate, isPending }] = useAtom(migrateAssetMutationAtom);
  const [confirm, setConfirm] = useState(false);
  if (!twin) return null;

  async function merge() {
    try {
      const result = await migrate({ assetId: asset.id, replaceGeneric: twin === 'differs' });
      setConfirm(false);
      const linked = result.linked_agents.join(', ') || 'no agent yet';
      toast.success('Merged', {
        description: `${result.asset.title} is on disk once, in ~/.agents/skills, linked into ${linked}.`,
      });
    } catch (err) {
      toast.error("Couldn't merge", { description: apiErrorMessage(err) });
    }
  }

  return (
    // Lives inside a clickable row or card: its clicks (and the dialog's) must not navigate.
    <span className="contents" onClick={swallow} onKeyDown={(e) => e.stopPropagation()}>
      <Button
        size="sm"
        variant="outline"
        className="h-6 px-2"
        disabled={isPending}
        onClick={() => (twin === 'differs' ? setConfirm(true) : void merge())}
        title={
          twin === 'identical'
            ? 'Turn this folder into a link to the generic copy'
            : 'Replace the generic copy with this one, then link it everywhere'
        }
      >
        <GitMerge /> {isPending ? 'Merging…' : 'Merge'}
      </Button>
      {twin === 'differs' ? (
        <MakeGenericDialog
          open={confirm}
          name={asset.name}
          provider={asset.provider}
          conflict
          pending={isPending}
          onConfirm={merge}
          onCancel={() => setConfirm(false)}
        />
      ) : null}
    </span>
  );
}
