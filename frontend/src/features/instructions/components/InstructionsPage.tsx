import { useEffect, useState } from 'react';
import { useBlocker, useSearchParams } from 'react-router-dom';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, FileText, Info, Pencil, Save, X } from 'lucide-react';
import type { AgentId } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { CodeEditor } from '~/components/CodeEditor';
import { UnsavedChangesDialog } from '~/components/UnsavedChangesDialog';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { absoluteDate } from '~/lib/datetime';
import { shortenPath } from '~/lib/paths';
import { agentIdLabel, isAgentId } from '~/features/settings/agents';
import { appSettingsQueryAtom } from '~/features/settings/queries';
import {
  instructionsQueryAtom,
  instructionsQueryKey,
  updateInstructionsMutationAtom,
} from '../queries';

const AGENT_TABS: { id: AgentId; label: string; fileName: string }[] = [
  { id: 'claude', label: 'Claude Code', fileName: 'CLAUDE.md' },
  { id: 'codex', label: 'Codex', fileName: 'AGENTS.md' },
];

function tabFor(agent: AgentId) {
  return AGENT_TABS.find((tab) => tab.id === agent) ?? AGENT_TABS[0];
}

/** Each agent's global instructions file — CLAUDE.md for Claude Code, AGENTS.md for Codex. */
export function InstructionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [{ data: settings, isPending: isSettingsPending }] = useAtom(appSettingsQueryAtom);

  const requested = searchParams.get('agent');
  const fallback = isAgentId(settings?.assistant_agent) ? settings.assistant_agent : 'claude';
  const agent: AgentId | null = isAgentId(requested)
    ? requested
    : isSettingsPending
      ? null
      : fallback;

  function setAgent(next: string) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        params.set('agent', next);
        return params;
      },
      { replace: true },
    );
  }

  // Pin the default into the URL, so switching the assistant mid-edit can't swap the file under you.
  useEffect(() => {
    if (agent && requested !== agent) setAgent(agent);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, requested]);

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <Tabs value={agent ?? ''} onValueChange={setAgent}>
        <TabsList aria-label="Agent">
          {AGENT_TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id}>
              {tab.label} · {tab.fileName}
            </TabsTrigger>
          ))}
        </TabsList>
        {agent ? (
          <TabsContent value={agent} className="pt-5">
            <InstructionsPanel
              key={agent}
              agent={agent}
              agentLabel={agentIdLabel(agent, settings?.agents)}
            />
          </TabsContent>
        ) : (
          <PanelSkeleton />
        )}
      </Tabs>
    </div>
  );
}

function PanelSkeleton() {
  return (
    <div className="space-y-4 pt-5">
      <Skeleton className="h-8 w-1/2" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-72 w-full" />
    </div>
  );
}

/** Keyed by agent, so a tab switch starts from a clean view rather than the other file's draft. */
function InstructionsPanel({ agent, agentLabel }: { agent: AgentId; agentLabel: string }) {
  const [{ data, isPending, isError, error }] = useAtom(instructionsQueryAtom(agent));
  const [{ mutateAsync, isPending: isSaving }] = useAtom(updateInstructionsMutationAtom);
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraft] = useState('');

  const dirty = mode === 'edit' && data != null && draft !== data.content;
  const fileName = data?.file_name ?? tabFor(agent).fileName;

  // Warn on a hard reload / tab close while there are unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // Blocks the agent tab switch too: it is a search-param navigation.
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
      const updated = await mutateAsync({ agent, content: draft });
      queryClient.setQueryData(instructionsQueryKey(agent), updated);
      // One file behind both agents: the other tab's copy is now stale too.
      if (updated.same_file_as) void queryClient.invalidateQueries({ queryKey: ['instructions'] });
      toast.success(`${updated.file_name} saved`, {
        description: updated.same_file_as
          ? 'Claude Code and Codex both read this file.'
          : shortenPath(updated.path),
      });
      setMode('view');
      setDraft('');
    } catch (err) {
      toast.error(`Could not save ${fileName}`, { description: apiErrorMessage(err) });
    }
  }

  if (isPending) return <PanelSkeleton />;

  if (isError || !data) {
    return (
      <EmptyState
        icon={<AlertTriangle className="size-8" />}
        title={`Couldn't load ${fileName}`}
        description={apiErrorMessage(error)}
      />
    );
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-3 border-b pb-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">{data.file_name}</h1>
            <p className="text-sm text-muted-foreground">
              Global instructions {agentLabel} loads at the start of every session on this machine.
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
          <code className="font-mono" title={data.path}>
            {shortenPath(data.path)}
          </code>
          {data.exists && data.updated_at ? (
            <span>Updated {absoluteDate(data.updated_at)}</span>
          ) : (
            <span>Not created yet</span>
          )}
        </div>
      </header>

      {data.shadowed_by ? (
        <div
          role="alert"
          className="flex gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <p>
            <span className="font-medium">AGENTS.override.md takes precedence.</span> {agentLabel}{' '}
            reads{' '}
            <code className="font-mono text-xs" title={data.shadowed_by}>
              {shortenPath(data.shadowed_by)}
            </code>{' '}
            instead of this file, so edits here have no effect until it is removed or emptied.
          </p>
        </div>
      ) : null}

      {data.same_file_as ? (
        <div className="flex gap-2 rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" />
          <p>
            Same file as{' '}
            <code className="font-mono text-xs text-foreground" title={data.same_file_as}>
              {shortenPath(data.same_file_as)}
            </code>{' '}
            — edits here apply to both Claude Code and Codex.
          </p>
        </div>
      ) : null}

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
          title={`No global ${data.file_name} yet`}
          description={`Create one to give every ${agentLabel} session standing instructions.`}
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
