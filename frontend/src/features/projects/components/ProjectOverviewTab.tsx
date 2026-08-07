import { useState } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Pencil, Save, X, Check } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { MarkdownView } from '~/components/MarkdownView';
import { CodeEditor } from '~/components/CodeEditor';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDate } from '~/lib/datetime';
import { updateProjectMutationAtom } from '../queries';
import { LinkedAssets } from './LinkedAssets';
import { FlowSection } from './FlowSection';

export function ProjectOverviewTab({ project }: { project: Project }) {
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
      <EditableName project={project} />
      <EditableGoal project={project} />
      <LinkedAssets project={project} />
      <FlowSection project={project} />
    </div>
  );
}

function EditableName({ project }: { project: Project }) {
  const [{ mutateAsync: update, isPending }] = useAtom(updateProjectMutationAtom);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project.name);

  function start() {
    setDraft(project.name);
    setEditing(true);
  }

  async function save() {
    const trimmed = draft.trim();
    if (!trimmed || trimmed === project.name) {
      setEditing(false);
      return;
    }
    try {
      const updated = await update({ projectId: project.id, body: { name: trimmed } });
      queryClient.setQueryData(['project', project.id], updated);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setEditing(false);
      toast.success('Project renamed');
    } catch (err) {
      toast.error('Rename failed', { description: apiErrorMessage(err) });
    }
  }

  if (editing) {
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Project name"
          autoFocus
          className="h-10 text-lg font-semibold"
        />
        <Button type="submit" size="icon" aria-label="Save name" disabled={isPending}>
          <Check />
        </Button>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Cancel rename"
          onClick={() => setEditing(false)}
          disabled={isPending}
        >
          <X />
        </Button>
      </form>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
      <button
        type="button"
        aria-label="Edit name"
        onClick={start}
        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <Pencil className="size-4" />
      </button>
      <span className="ml-auto text-xs text-muted-foreground">
        Updated {absoluteDate(project.updated_at)}
      </span>
    </div>
  );
}

function EditableGoal({ project }: { project: Project }) {
  const [{ mutateAsync: update, isPending }] = useAtom(updateProjectMutationAtom);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  function start() {
    setDraft(project.goal);
    setEditing(true);
  }

  async function save() {
    try {
      const updated = await update({ projectId: project.id, body: { goal: draft } });
      queryClient.setQueryData(['project', project.id], updated);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setEditing(false);
      toast.success('Goal saved');
    } catch (err) {
      toast.error('Save failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Goal</h2>
        {editing ? (
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={isPending}>
              <X /> Cancel
            </Button>
            <Button size="sm" onClick={save} disabled={isPending}>
              <Save /> {isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={start}>
            <Pencil /> {project.goal.trim() ? 'Edit goal' : 'Add goal'}
          </Button>
        )}
      </div>

      {editing ? (
        <CodeEditor
          value={draft}
          onChange={setDraft}
          ariaLabel="Project goal (markdown)"
          minHeight="12rem"
          maxHeight="28rem"
        />
      ) : project.goal.trim() ? (
        <MarkdownView content={project.goal} />
      ) : (
        <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No goal yet. Describe the scenario you want your skills and agents to support — you can use
          markdown and Mermaid diagrams.
        </p>
      )}
    </section>
  );
}
