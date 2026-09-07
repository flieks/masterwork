import { Copy } from 'lucide-react';
import type { WorkItem, WorkItemStartResponse } from '~/api/generated';
import { Button } from '~/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { toast } from '~/components/ui/sonner';

export interface StartedItem {
  item: WorkItem;
  response: WorkItemStartResponse;
}

interface StartPromptDialogProps {
  started: StartedItem | null;
  onClose: () => void;
}

export function StartPromptDialog({ started, onClose }: StartPromptDialogProps) {
  async function copyPrompt(prompt: string) {
    try {
      await navigator.clipboard.writeText(prompt);
      toast.success('Prompt copied');
    } catch {
      toast.error('Could not copy the prompt', {
        description: 'Select the text and copy it manually.',
      });
    }
  }

  return (
    <Dialog open={started !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        {started ? (
          <>
            <DialogHeader>
              <DialogTitle>Session prompt for #{started.item.external_id}</DialogTitle>
              <DialogDescription>
                {started.response.launched
                  ? 'The session was launched; this is the prompt it received.'
                  : 'Launch is deferred — masterwork has no session-launch path yet, so nothing was started. Copy this prompt into Claude Code to run it yourself.'}
              </DialogDescription>
            </DialogHeader>

            {/* Assembled from DevOps text: plain, never markdown-rendered. */}
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
              {started.response.prompt}
            </pre>

            <DialogFooter>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              <Button onClick={() => void copyPrompt(started.response.prompt)}>
                <Copy /> Copy prompt
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
