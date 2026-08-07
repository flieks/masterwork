import { useEffect, useState } from 'react';
import { useBlocker } from 'react-router-dom';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, FileText, Pencil, Save, X } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { CodeEditor } from '~/components/CodeEditor';
import { UnsavedChangesDialog } from '~/components/UnsavedChangesDialog';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDate } from '~/lib/datetime';
import { shortenPath } from '~/lib/paths';
import {
  INSTRUCTIONS_QUERY_KEY,
  instructionsQueryAtom,
  updateInstructionsMutationAtom,
} from '../queries';

/** The global CLAUDE.md — read and edit the instructions every session loads. */
export function InstructionsPage() {
  const [{ data, isPending, isError, error }] = useAtom(instructionsQueryAtom);
  const [{ mutateAsync, isPending: isSaving }] = useAtom(updateInstructionsMutationAtom);
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraft] = useState('');

  const dirty = mode === 'edit' && data != null && draft !== data.content;

  // Warn on a hard reload / tab close while there are unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const blocker = useBlocker(dirty);

  function startEdit() {
    if (!data) return;
    setDraft(data.content);
    setMode('edit');
  }

  function cancelEdit() {
    setMode('view');
    setDraft('');
  }

  async function save() {
    try {
      const updated = await mutateAsync(draft);
      queryClient.setQueryData(INSTRUCTIONS_QUERY_KEY, updated);
      toast.success('Saved', { description: 'CLAUDE.md was updated.' });
      setMode('view');
      setDraft('');
    } catch (err) {
      toast.error('Save failed', { description: apiErrorMessage(err) });
    }
  }

  if (isPending) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 p-6">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="mx-auto w-full max-w-4xl p-6">
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load CLAUDE.md"
          description={apiErrorMessage(error)}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <header className="flex flex-col gap-3 border-b pb-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">CLAUDE.md</h1>
            <p className="text-sm text-muted-foreground">
              Global instructions prepended to every Claude Code session on this machine.
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            {mode === 'view' ? (
              <Button size="sm" variant="outline" onClick={startEdit}>
                <Pencil /> Edit
              </Button>
            ) : (
              <>
                <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={isSaving}>
                  <X /> Cancel
                </Button>
                <Button size="sm" onClick={save} disabled={isSaving || !dirty}>
                  <Save /> {isSaving ? 'Saving…' : 'Save'}
                </Button>
              </>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          <code className="font-mono">{shortenPath(data.path)}</code>
          {data.exists && data.updated_at ? (
            <span>Updated {absoluteDate(data.updated_at)}</span>
          ) : (
            <span>Not created yet</span>
          )}
        </div>
      </header>

      {mode === 'edit' ? (
        <CodeEditor
          value={draft}
          onChange={setDraft}
          ariaLabel="Markdown editor"
          minHeight="30rem"
        />
      ) : data.exists ? (
        <MarkdownView content={data.content} />
      ) : (
        <EmptyState
          icon={<FileText className="size-8" />}
          title="No global CLAUDE.md yet"
          description="Create one to give every Claude Code session standing instructions."
          action={
            <Button size="sm" onClick={startEdit}>
              <Pencil /> Create file
            </Button>
          }
        />
      )}

      <UnsavedChangesDialog
        open={blocker.state === 'blocked'}
        onDiscard={() => blocker.proceed?.()}
        onKeepEditing={() => blocker.reset?.()}
      />
    </div>
  );
}
