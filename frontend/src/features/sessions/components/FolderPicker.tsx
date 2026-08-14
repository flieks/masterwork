import { useAtom, useSetAtom } from 'jotai';
import { AlertTriangle, CornerLeftUp } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { browseDirectoriesQueryAtom, browsePathAtom, updateSettingsMutationAtom } from '../queries';

interface FolderPickerProps {
  /** Called with the shown directory once it's saved as projects_root. */
  onPicked: (path: string) => void;
}

/** Breadcrumb segments for a resolved absolute path, root first. */
function crumbs(path: string): { label: string; path: string }[] {
  const parts = path.split('/').filter(Boolean);
  let acc = '';
  return [
    { label: '/', path: '/' },
    ...parts.map((part) => {
      acc += `/${part}`;
      return { label: part, path: acc };
    }),
  ];
}

/** Nested panel (not a Popover — @radix-ui/react-popover isn't a dependency here)
 * for setting projects_root by browsing instead of typing a path. */
export function FolderPicker({ onPicked }: FolderPickerProps) {
  const [{ data, isPending, isError, refetch }] = useAtom(browseDirectoriesQueryAtom);
  const setBrowsePath = useSetAtom(browsePathAtom);
  const [{ mutateAsync: saveSettings, isPending: isSaving }] = useAtom(updateSettingsMutationAtom);

  if (isPending) {
    return (
      <div className="space-y-2 rounded-md border p-3">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <p className="flex items-center gap-2 rounded-md border p-3 text-sm text-muted-foreground">
        <AlertTriangle className="size-4" /> Couldn't browse that folder.
        <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </p>
    );
  }

  const entries = data.entries ?? [];
  const parent = data.parent ?? null;

  async function useThisFolder() {
    if (!data) return;
    try {
      await saveSettings({ projects_root: data.path });
      toast.success('Projects root updated');
      onPicked(data.path);
    } catch (err) {
      toast.error('Could not update the projects root', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-1 text-sm">
        {crumbs(data.path).map((crumb, i) => (
          <span key={crumb.path} className="flex items-center gap-1">
            {i > 0 && <span className="text-muted-foreground">/</span>}
            <button
              type="button"
              className="rounded px-1 hover:underline"
              onClick={() => setBrowsePath(crumb.path)}
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </div>

      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setBrowsePath(parent)}
        disabled={parent === null}
      >
        <CornerLeftUp className="size-4" /> Up one level
      </Button>

      <ul className="max-h-48 space-y-0.5 overflow-y-auto">
        {entries.length === 0 && (
          <li className="px-2 py-1 text-sm text-muted-foreground">No subfolders here.</li>
        )}
        {entries.map((entry) => (
          <li key={entry.path}>
            <button
              type="button"
              className="w-full rounded px-2 py-1 text-left text-sm hover:bg-accent"
              onClick={() => setBrowsePath(entry.path)}
            >
              {entry.name}
            </button>
          </li>
        ))}
      </ul>

      <Button type="button" onClick={() => void useThisFolder()} disabled={isSaving}>
        {isSaving ? 'Saving…' : 'Use this folder'}
      </Button>
    </div>
  );
}
