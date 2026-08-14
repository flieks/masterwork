import { ExternalLink } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { isHttpUrl } from '../queries';

interface WorkItemDetailDialogProps {
  item: WorkItem | null;
  onClose: () => void;
  onStart: (item: WorkItem) => void;
  starting: boolean;
}

export function WorkItemDetailDialog({
  item,
  onClose,
  onStart,
  starting,
}: WorkItemDetailDialogProps) {
  return (
    <Dialog open={item !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        {item ? (
          <>
            <DialogHeader>
              {/* Every field below is DevOps text: rendered as text, never as markup. */}
              <DialogTitle>{item.title}</DialogTitle>
              <DialogDescription className="flex items-center gap-2">
                <span className="font-mono">#{item.external_id}</span>
                {isHttpUrl(item.external_url) ? (
                  <a
                    href={item.external_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-1 underline underline-offset-4 hover:text-foreground"
                  >
                    Open in Azure DevOps <ExternalLink className="size-3" />
                  </a>
                ) : null}
              </DialogDescription>
            </DialogHeader>

            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{item.item_type}</Badge>
              <Badge variant="muted">{item.state}</Badge>
              {item.iteration ? <Badge variant="outline">{item.iteration}</Badge> : null}
              {item.priority !== null ? (
                <Badge variant="outline">Priority {item.priority}</Badge>
              ) : null}
              {item.pulled_as_parent ? <Badge variant="muted">context</Badge> : null}
              {(item.tags ?? []).map((tag) => (
                <Badge key={tag} variant="outline">
                  {tag}
                </Badge>
              ))}
            </div>

            <div className="max-h-[50vh] space-y-4 overflow-auto">
              <section className="space-y-1.5">
                <h3 className="text-sm font-medium">Description</h3>
                <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
                  {item.description_md || 'No description on the work item.'}
                </pre>
              </section>

              {item.acceptance_md ? (
                <section className="space-y-1.5">
                  <h3 className="text-sm font-medium">Acceptance criteria</h3>
                  <pre className="whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
                    {item.acceptance_md}
                  </pre>
                </section>
              ) : null}
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              {/* A context row belongs to someone else — nothing to start here. */}
              {item.pulled_as_parent ? null : (
                <Button disabled={starting} onClick={() => onStart(item)}>
                  {starting ? 'Starting…' : 'Start session'}
                </Button>
              )}
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
