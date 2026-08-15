import { useLayoutEffect, useState } from 'react';
import { atom, useAtom, useSetAtom } from 'jotai';
import { atomWithQuery } from 'jotai-tanstack-query';
import { AlertTriangle, ChevronRight, CornerLeftUp, Folder, FolderOpen, Home } from 'lucide-react';
import { api } from '~/api/client';
import type { DirectoryListing } from '~/api/generated';
import { Button } from '~/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '~/components/ui/dialog';
import { Skeleton } from '~/components/ui/skeleton';
import { cn } from '~/lib/utils';

/** The directory being browsed. Null means "let the server default to projects_root". */
const browsePathAtom = atom<string | null>(null);

/** Each visited directory is cached under its own path, so revisiting one is instant.
 * Only read while the picker body is mounted, so a closed picker never calls `browse`. */
const browseDirectoriesQueryAtom = atomWithQuery((get) => ({
  queryKey: ['browseDirectories', get(browsePathAtom)],
  queryFn: async (): Promise<DirectoryListing> =>
    (await api.launcher.browseDirectories(get(browsePathAtom) ?? undefined)).data,
}));

export interface FolderShortcut {
  label: string;
  path: string;
}

interface FolderPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Where browsing starts each time it opens. Null lets the server pick. */
  initialPath?: string | null;
  title?: string;
  description?: string;
  confirmLabel?: string;
  /** Extra quick-access rows above the home shortcut the server reports. */
  shortcuts?: FolderShortcut[];
  isConfirming?: boolean;
  onConfirm: (path: string) => void;
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

/** A reusable directory chooser shaped like a desktop file dialog: breadcrumb
 * toolbar, quick-access sidebar, a scrolling folder list, and a footer that
 * confirms the folder currently shown. Browsing is server-side — the browser
 * never sees absolute paths of its own. */
export function FolderPickerDialog({
  open,
  onOpenChange,
  initialPath = null,
  title = 'Choose a folder',
  description = 'Pick a folder on the machine running the backend.',
  confirmLabel = 'Use this folder',
  shortcuts = [],
  isConfirming = false,
  onConfirm,
}: FolderPickerDialogProps) {
  const setBrowsePath = useSetAtom(browsePathAtom);
  // The body may only mount once the starting path is in place, otherwise the
  // first render would browse wherever the previous open left off.
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    if (open) {
      setBrowsePath(initialPath);
      setReady(true);
    } else {
      setReady(false);
    }
  }, [open, initialPath, setBrowsePath]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl gap-0 overflow-hidden p-0">
        <div className="border-b px-5 py-4">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="mt-1">{description}</DialogDescription>
        </div>
        {open && ready && (
          <FolderPickerBody
            confirmLabel={confirmLabel}
            shortcuts={shortcuts}
            isConfirming={isConfirming}
            onConfirm={onConfirm}
            onCancel={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

interface FolderPickerBodyProps {
  confirmLabel: string;
  shortcuts: FolderShortcut[];
  isConfirming: boolean;
  onConfirm: (path: string) => void;
  onCancel: () => void;
}

function FolderPickerBody({
  confirmLabel,
  shortcuts,
  isConfirming,
  onConfirm,
  onCancel,
}: FolderPickerBodyProps) {
  const [{ data, isPending, isError, refetch }] = useAtom(browseDirectoriesQueryAtom);
  const setBrowsePath = useSetAtom(browsePathAtom);

  const entries = data?.entries ?? [];
  const parent = data?.parent ?? null;
  const quickLinks: (FolderShortcut & { isHome?: boolean })[] = [
    ...(data?.home ? [{ label: 'Home', path: data.home, isHome: true }] : []),
    ...shortcuts,
  ];

  return (
    <>
      <div className="flex items-center gap-2 border-b bg-muted/70 px-3 py-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="size-8 shrink-0 p-0"
          onClick={() => setBrowsePath(parent)}
          disabled={parent === null}
          title="Up one level"
        >
          <CornerLeftUp className="size-4" />
          <span className="sr-only">Up one level</span>
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto whitespace-nowrap">
          {data &&
            crumbs(data.path).map((crumb, i) => (
              <span key={crumb.path} className="flex shrink-0 items-center">
                {i > 0 && <ChevronRight className="size-3.5 text-muted-foreground/60" />}
                <button
                  type="button"
                  className="rounded px-1.5 py-0.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[current=true]:font-medium aria-[current=true]:text-foreground"
                  aria-current={i === crumbs(data.path).length - 1}
                  onClick={() => setBrowsePath(crumb.path)}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
        </div>
      </div>

      <div className="flex min-h-0">
        {quickLinks.length > 0 && (
          <nav className="w-44 shrink-0 space-y-0.5 border-r bg-muted/70 p-2">
            {quickLinks.map((link) => (
              <button
                key={`${link.label}:${link.path}`}
                type="button"
                title={link.path}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  link.path === data?.path
                    ? 'bg-accent font-medium text-accent-foreground shadow-sm'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                )}
                onClick={() => setBrowsePath(link.path)}
              >
                {link.isHome ? (
                  <Home className="size-4 shrink-0" />
                ) : (
                  <FolderOpen className="size-4 shrink-0" />
                )}
                <span className="truncate">{link.label}</span>
              </button>
            ))}
          </nav>
        )}

        {/* The list sits a level below the surrounding chrome so the two read as
            separate surfaces in both themes, not one flat panel. */}
        <div className="h-72 min-w-0 flex-1 overflow-y-auto bg-background p-2">
          {isPending ? (
            <div className="space-y-1.5 p-1">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : isError || !data ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              <p className="flex items-center gap-2">
                <AlertTriangle className="size-4" /> Couldn't browse that folder.
              </p>
              <Button type="button" variant="outline" size="sm" onClick={() => void refetch()}>
                Retry
              </Button>
            </div>
          ) : entries.length === 0 ? (
            <p className="flex h-full items-center justify-center text-sm text-muted-foreground">
              No subfolders here.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {entries.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => setBrowsePath(entry.path)}
                  >
                    <Folder className="size-4 shrink-0 text-muted-foreground group-hover:text-accent-foreground" />
                    <span className="truncate">{entry.name}</span>
                    <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 border-t bg-muted/70 px-5 py-3">
        <p className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground" title={data?.path}>
          {data?.path ?? ''}
        </p>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="button"
          onClick={() => data && onConfirm(data.path)}
          disabled={!data || isConfirming}
        >
          {isConfirming ? 'Saving…' : confirmLabel}
        </Button>
      </div>
    </>
  );
}
