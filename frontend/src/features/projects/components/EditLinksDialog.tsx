import { useEffect, useMemo, useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Search, Sparkles } from 'lucide-react';
import type { AssetSummary, Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { toast } from '~/components/ui/sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { apiErrorMessage } from '~/api/client';
import { cn } from '~/lib/utils';
import { suggestLinksMutationAtom, updateProjectMutationAtom } from '../queries';

interface EditLinksDialogProps {
  project: Project;
  allAssets: AssetSummary[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** At or above this the model is recommending the asset, so pre-check it;
 * below it the asset is listed as a borderline candidate for the user to judge. */
const RECOMMENDED_AT = 60;

interface Suggestion {
  reason: string;
  confidence: number;
}

function confidenceStyle(confidence: number): string {
  if (confidence >= 85) return 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400';
  if (confidence >= RECOMMENDED_AT) return 'bg-sky-500/15 text-sky-700 dark:text-sky-400';
  return 'bg-amber-500/15 text-amber-700 dark:text-amber-400';
}

export function EditLinksDialog({ project, allAssets, open, onOpenChange }: EditLinksDialogProps) {
  const [{ mutateAsync: update, isPending }] = useAtom(updateProjectMutationAtom);
  const [{ mutateAsync: suggest, isPending: isSuggesting }] = useAtom(suggestLinksMutationAtom);
  const queryClient = useQueryClient();

  const [selected, setSelected] = useState<Set<string>>(new Set(project.asset_ids));
  const [query, setQuery] = useState('');
  // asset_id -> the model's score + reasoning; shown under the asset until save/close.
  const [suggestions, setSuggestions] = useState<Map<string, Suggestion>>(new Map());
  // Rows whose title/description/reason are shown in full instead of clamped.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Re-seed the selection whenever the dialog (re)opens or the project changes.
  useEffect(() => {
    if (open) {
      setSelected(new Set(project.asset_ids));
      setQuery('');
      setSuggestions(new Map());
      setExpanded(new Set());
    }
  }, [open, project.asset_ids]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    // Scored assets float to the top, strongest first, so the cut line is visible.
    const list = [...allAssets].sort(
      (a, b) =>
        (suggestions.get(b.id)?.confidence ?? -1) - (suggestions.get(a.id)?.confidence ?? -1) ||
        a.title.localeCompare(b.title),
    );
    if (!q) return list;
    return list.filter(
      (a) =>
        a.title.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q),
    );
  }, [allAssets, query, suggestions]);

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function suggestAssets() {
    try {
      const res = await suggest(project.id);
      const map = new Map<string, Suggestion>(
        res.suggestions.map((s) => [
          s.asset_id,
          { reason: s.reason ?? '', confidence: s.confidence ?? RECOMMENDED_AT },
        ]),
      );
      // The model returns the COMPLETE recommended toolkit plus the borderline
      // candidates it rejected — pre-check only the recommended band; the rest
      // stay listed and unchecked for the user to judge.
      const recommended = [...map].filter(([, s]) => s.confidence >= RECOMMENDED_AT);
      setSuggestions(map);
      setSelected(new Set(recommended.map(([id]) => id)));
      setQuery('');
      const borderline = map.size - recommended.length;
      toast.success(`Selected ${recommended.length} assets`, {
        description: borderline
          ? `${borderline} borderline ${borderline === 1 ? 'candidate is' : 'candidates are'} listed unchecked — review, adjust, then save.`
          : 'Review the selection, adjust, then save.',
      });
    } catch (err) {
      toast.error('Suggestion failed', { description: apiErrorMessage(err) });
    }
  }

  async function save() {
    try {
      const updated = await update({
        projectId: project.id,
        body: { asset_ids: [...selected] },
      });
      queryClient.setQueryData(['project', project.id], updated);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      onOpenChange(false);
      toast.success('Linked assets updated');
    } catch (err) {
      toast.error('Update failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit linked assets</DialogTitle>
          <DialogDescription>
            Choose the skills and agents that serve this project.
          </DialogDescription>
        </DialogHeader>

        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              aria-label="Search assets"
              placeholder="Search assets…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          <Button
            variant="outline"
            onClick={suggestAssets}
            disabled={isSuggesting || isPending}
            title="Let the model pick the toolkit for this goal; you review and save."
          >
            <Sparkles />
            {isSuggesting ? 'Suggesting…' : 'Suggest'}
          </Button>
        </div>

        <div className="max-h-[26rem] space-y-0.5 overflow-y-auto rounded-md border p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              No matching assets.
            </p>
          ) : (
            filtered.map((asset) => {
              const suggestion = suggestions.get(asset.id);
              const isOpen = expanded.has(asset.id);
              return (
                <label
                  key={asset.id}
                  className="flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-accent"
                >
                  <input
                    type="checkbox"
                    className="mt-1 size-4 shrink-0 accent-primary"
                    checked={selected.has(asset.id)}
                    onChange={() => toggle(asset.id)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-start gap-1.5 text-sm font-medium">
                      <span
                        title={asset.title}
                        className={cn('min-w-0', isOpen ? 'break-words' : 'truncate')}
                      >
                        {asset.title}
                      </span>
                      <span className="mt-0.5 shrink-0 rounded bg-muted px-1 text-[10px] uppercase text-muted-foreground">
                        {asset.kind}
                      </span>
                      {suggestion ? (
                        <span
                          title={
                            suggestion.confidence >= RECOMMENDED_AT
                              ? 'Recommended for this goal'
                              : 'Borderline — the goal may never trigger it'
                          }
                          className={cn(
                            'mt-0.5 ml-auto shrink-0 rounded-full px-1.5 text-[10px] font-semibold tabular-nums',
                            confidenceStyle(suggestion.confidence),
                          )}
                        >
                          {suggestion.confidence}%
                        </span>
                      ) : null}
                      {/* Inside a <label>, so suppress the implicit checkbox toggle. */}
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${asset.title}`}
                        title={isOpen ? 'Show less' : 'Show full description'}
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          toggleExpanded(asset.id);
                        }}
                        className={cn(
                          'mt-0.5 shrink-0 rounded text-muted-foreground hover:text-foreground',
                          !suggestion && 'ml-auto',
                        )}
                      >
                        <ChevronDown
                          className={cn('size-3.5 transition-transform', isOpen && 'rotate-180')}
                        />
                      </button>
                    </span>
                    {asset.description ? (
                      <span
                        title={asset.description}
                        className={cn(
                          'text-xs text-muted-foreground',
                          isOpen ? 'block whitespace-pre-wrap' : 'line-clamp-1',
                        )}
                      >
                        {asset.description}
                      </span>
                    ) : null}
                    {suggestion?.reason ? (
                      <span
                        title={suggestion.reason}
                        className={cn('text-xs text-primary', isOpen ? 'block' : 'line-clamp-2')}
                      >
                        ✦ {suggestion.reason}
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })
          )}
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">{selected.size} selected</span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
              Cancel
            </Button>
            <Button onClick={save} disabled={isPending}>
              {isPending ? 'Saving…' : 'Save links'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
