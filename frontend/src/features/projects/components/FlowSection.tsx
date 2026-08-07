import { useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Pencil, Save, X, Workflow } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { CodeEditor } from '~/components/CodeEditor';
import { MermaidView } from '~/components/MermaidView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { updateProjectMutationAtom } from '../queries';

const PLACEHOLDER = `flowchart TD
  A[Trigger] --> B[Skill]
  B --> C[Result]`;

export function FlowSection({ project }: { project: Project }) {
  const [{ mutateAsync: update, isPending }] = useAtom(updateProjectMutationAtom);
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraft] = useState('');

  const flow = project.flow_mermaid ?? '';

  function startEdit() {
    setDraft(flow);
    setMode('edit');
  }

  async function save() {
    try {
      const trimmed = draft.trim();
      const updated = await update({
        projectId: project.id,
        body: { flow_mermaid: trimmed ? draft : null },
      });
      queryClient.setQueryData(['project', project.id], updated);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setMode('view');
      toast.success('Flow diagram saved');
    } catch (err) {
      toast.error('Save failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Flow</h2>
        {mode === 'view' ? (
          <Button size="sm" variant="outline" onClick={startEdit}>
            <Pencil /> {flow.trim() ? 'Edit flow' : 'Add flow'}
          </Button>
        ) : (
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setMode('view')} disabled={isPending}>
              <X /> Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={isPending}>
              <Save /> {isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        )}
      </div>

      {mode === 'edit' ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <CodeEditor
            value={draft}
            onChange={setDraft}
            ariaLabel="Flow diagram source"
            minHeight="16rem"
            maxHeight="28rem"
          />
          <div className="rounded-md border p-3">
            <p className="mb-2 text-xs font-medium text-muted-foreground">Live preview</p>
            {draft.trim() ? (
              <MermaidView source={draft} />
            ) : (
              <p className="text-sm text-muted-foreground">Type a Mermaid diagram to preview it.</p>
            )}
          </div>
        </div>
      ) : flow.trim() ? (
        <div className="rounded-md border p-3">
          <MermaidView source={flow} />
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2 rounded-md border border-dashed p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Workflow className="size-4" />
            No flow diagram yet.
          </div>
          <p className="text-sm text-muted-foreground">
            Ask the project chat to analyze your scenario and propose a flow diagram, or add one
            manually. Example:
          </p>
          <pre className="w-full overflow-x-auto rounded bg-muted px-3 py-2 font-mono text-xs">
            {PLACEHOLDER}
          </pre>
        </div>
      )}
    </section>
  );
}
