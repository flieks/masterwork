import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '~/components/ui/alert-dialog';

interface MakeGenericDialogProps {
  open: boolean;
  name: string;
  /** Where the skill lives now: "claude" or "codex". */
  provider: string;
  /**
   * The generic folder already holds a different copy: the dialog re-arms
   * into replacing it, which the first press must never do on its own.
   */
  conflict: boolean;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const HOME: Record<string, string> = { claude: '~/.claude/skills', codex: '~/.codex/skills' };

/** Confirm moving one agent's skill into the shared ~/.agents/skills folder. */
export function MakeGenericDialog({
  open,
  name,
  provider,
  conflict,
  pending,
  onConfirm,
  onCancel,
}: MakeGenericDialogProps) {
  const from = HOME[provider] ?? provider;
  const target = `~/.agents/skills/${name}`;
  return (
    <AlertDialog open={open} onOpenChange={(next) => (!next && !pending ? onCancel() : undefined)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {conflict ? `Replace the generic copy of “${name}”?` : `Make “${name}” generic?`}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm text-muted-foreground">
              {conflict ? (
                <p>
                  <code className="font-mono">{target}</code> already exists and differs from this
                  one. Replacing it deletes that copy for good and moves this one in its place.
                </p>
              ) : (
                <p>
                  The skill folder moves from{' '}
                  <code className="font-mono">
                    {from}/{name}
                  </code>{' '}
                  to <code className="font-mono">{target}</code>, the folder every coding agent can
                  share. Its <code className="font-mono">name</code> is set to match the folder;
                  nothing else in the file changes. An identical copy already there is kept as is.
                </p>
              )}
              <p>
                The old location becomes a link to the new one, and Codex and Claude each get a link
                in their own folder — so the skill is on disk once and every agent still finds it.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onCancel} disabled={pending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            // Radix closes on click; the dialog must outlive the request to re-arm on a 409.
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
            disabled={pending}
            className={
              conflict ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90' : ''
            }
          >
            {pending ? 'Moving…' : conflict ? 'Replace and make generic' : 'Make generic'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
