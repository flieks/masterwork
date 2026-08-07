import { useEffect, useState } from 'react';
import { Link, useBlocker, useParams, useSearchParams } from 'react-router-dom';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Lock, Pencil, Save, X, AlertTriangle } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { CodeEditor } from '~/components/CodeEditor';
import { toast } from '~/components/ui/sonner';
import { UnsavedChangesDialog } from '~/components/UnsavedChangesDialog';
import { apiErrorMessage } from '~/api/client';
import { absoluteDate } from '~/lib/datetime';
import { shortenPath } from '~/lib/paths';
import { splitFrontmatter } from '~/lib/frontmatter';
import { AssetChatPanel } from '~/features/chat';
import { ProviderBadge } from './ProviderBadge';
import { ModelBadge } from './ModelBadge';
import { AssetDiagramSection } from './AssetDiagramSection';
import { AgentSkillsUsed } from './AgentSkillsUsed';
import {
  assetDetailQueryAtom,
  assetListPath,
  buildAssetId,
  updateAssetMutationAtom,
  type AssetKind,
} from '../queries';

export function AssetDetailPage({ kind }: { kind: AssetKind }) {
  const { name = '' } = useParams();
  const [searchParams] = useSearchParams();
  // Plugin assets link here with ?p=claude-plugin; global assets omit it.
  const assetId = buildAssetId(kind, name, searchParams.get('p') ?? undefined);

  const [{ data, isPending, isError, error }] = useAtom(assetDetailQueryAtom(assetId));
  const [{ mutateAsync, isPending: isSaving }] = useAtom(updateAssetMutationAtom);
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

  // Block in-app navigation while there are unsaved edits.
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
      const updated = await mutateAsync({ assetId, content: draft });
      queryClient.setQueryData(['asset', assetId], updated);
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      toast.success('Saved', { description: `${updated.title} was updated.` });
      setMode('view');
      setDraft('');
    } catch (err) {
      toast.error('Save failed', { description: apiErrorMessage(err) });
    }
  }

  if (isPending) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 p-6">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    const notFound = apiErrorMessage(error).toLowerCase().includes('not found');
    return (
      <div className="mx-auto w-full max-w-4xl p-6">
        <BackLink kind={kind} />
        <EmptyState
          className="mt-4"
          icon={<AlertTriangle className="size-8" />}
          title={
            notFound ? `${kind === 'skill' ? 'Skill' : 'Agent'} not found` : "Couldn't load asset"
          }
          description={notFound ? assetId : apiErrorMessage(error)}
        />
      </div>
    );
  }

  const { frontmatter, body } = splitFrontmatter(data.content);

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <BackLink kind={kind} />

      <header className="flex flex-col gap-3 border-b pb-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-1.5">
            <h1 className="text-2xl font-semibold tracking-tight">{data.title}</h1>
            {data.description ? (
              <p className="text-sm text-muted-foreground">{data.description}</p>
            ) : null}
          </div>
          <div className="flex shrink-0 gap-2">
            {data.read_only ? (
              <span
                className="inline-flex items-center gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground"
                title="Plugin assets are managed by their marketplace and can't be edited here."
              >
                <Lock className="size-3.5" /> Read-only · plugin
              </span>
            ) : mode === 'view' ? (
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
          <ProviderBadge provider={data.provider} />
          <ModelBadge model={data.model} showInherit={kind === 'agent'} />
          <code className="font-mono">{shortenPath(data.path)}</code>
          <span>Updated {absoluteDate(data.updated_at)}</span>
        </div>
        {kind === 'agent' ? <AgentSkillsUsed content={data.content} /> : null}
      </header>

      <AssetChatPanel assetId={assetId} kindLabel={kind === 'skill' ? 'skill' : 'agent'} />

      {mode === 'view' ? (
        <div className="space-y-4">
          <AssetDiagramSection assetId={assetId} kind={kind} />
          {frontmatter ? (
            <details className="rounded-md border bg-muted/30">
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">
                Frontmatter
              </summary>
              <pre className="overflow-x-auto border-t px-3 py-2 font-mono text-xs">
                {frontmatter}
              </pre>
            </details>
          ) : null}
          <MarkdownView content={body} />
        </div>
      ) : (
        <CodeEditor
          value={draft}
          onChange={setDraft}
          ariaLabel="Markdown editor"
          minHeight="30rem"
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

function BackLink({ kind }: { kind: AssetKind }) {
  return (
    <Link
      to={assetListPath(kind)}
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" /> Back to {kind === 'skill' ? 'skills' : 'agents'}
    </Link>
  );
}
