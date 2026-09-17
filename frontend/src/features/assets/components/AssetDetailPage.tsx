import { useEffect, useState } from 'react';
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Lock, Pencil, Save, X, AlertTriangle, Share2 } from 'lucide-react';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Switch } from '~/components/ui/switch';
import { EmptyState } from '~/components/EmptyState';
import { MarkdownView } from '~/components/MarkdownView';
import { CodeEditor } from '~/components/CodeEditor';
import { toast } from '~/components/ui/sonner';
import { UnsavedChangesDialog } from '~/components/UnsavedChangesDialog';
import { apiErrorMessage, isConflictError } from '~/api/client';
import { shortenPath } from '~/lib/paths';
import { splitFrontmatter } from '~/lib/frontmatter';
import { AssetChatPanel } from '~/features/chat';
import { ProviderBadge } from './ProviderBadge';
import { AgentsBadge } from './AgentsBadge';
import { GenericTwinBadge } from './GenericTwinBadge';
import { CODEX_CONFIG_NOTE, DisabledBadge } from './DisabledBadge';
import { MakeGenericDialog } from './MakeGenericDialog';
import { ModelBadge } from './ModelBadge';
import { AssetDatesInline } from './AssetDates';
import { AssetDiagramSection } from './AssetDiagramSection';
import { AgentSkillsUsed } from './AgentSkillsUsed';
import { AssetUsageLog } from './AssetUsageLog';
import { loadedByPhrase } from '../agents';
import { UpstreamCard } from './UpstreamCard';
import {
  assetDetailPath,
  assetDetailQueryAtom,
  assetListPath,
  buildAssetId,
  isPluginProvider,
  isTomlAsset,
  migrateAssetMutationAtom,
  setAssetEnabledMutationAtom,
  updateAssetMutationAtom,
  type AssetKind,
} from '../queries';

// Skills in a folder masterwork may write to; a plugin's folder is its marketplace's.
const TOGGLEABLE_PROVIDERS = new Set(['claude', 'codex', 'generic']);

export function AssetDetailPage({ kind }: { kind: AssetKind }) {
  const { name = '' } = useParams();
  const [searchParams] = useSearchParams();
  // Non-Claude assets link here with ?p=<provider> (codex, generic, a plugin); Claude's omit it.
  const assetId = buildAssetId(kind, name, searchParams.get('p') ?? undefined);

  const [{ data, isPending, isError, error }] = useAtom(assetDetailQueryAtom(assetId));
  const [{ mutateAsync, isPending: isSaving }] = useAtom(updateAssetMutationAtom);
  const [{ mutateAsync: migrate, isPending: isMigrating }] = useAtom(migrateAssetMutationAtom);
  const [{ mutateAsync: setEnabled, isPending: isToggling }] = useAtom(setAssetEnabledMutationAtom);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [confirmGeneric, setConfirmGeneric] = useState(false);
  const [genericConflict, setGenericConflict] = useState(false);

  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraft] = useState('');
  // Kept beside the editor, not only in a toast: a TOML validation message is what the fix needs.
  const [saveError, setSaveError] = useState<string | null>(null);

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
    setSaveError(null);
    setMode('edit');
  }

  function cancelEdit() {
    setMode('view');
    setDraft('');
    setSaveError(null);
  }

  async function save() {
    setSaveError(null);
    try {
      const updated = await mutateAsync({ assetId, content: draft });
      queryClient.setQueryData(['asset', assetId], updated);
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      toast.success('Saved', { description: `${updated.title} was updated.` });
      setMode('view');
      setDraft('');
    } catch (err) {
      // Nothing was written server-side, so the draft stays open for the fix.
      setSaveError(apiErrorMessage(err));
      toast.error('Save failed', {
        description: 'Your draft is kept; the reason is above the editor.',
      });
    }
  }

  async function toggleEnabled(enabled: boolean) {
    try {
      const updated = await setEnabled({ assetId, enabled });
      toast.success(enabled ? 'Enabled' : 'Disabled', {
        description: enabled
          ? `${updated.title} is back in its skills folder and loads again.`
          : `${updated.title} moved to .disabled/; no agent loads it until you switch it back on.`,
      });
    } catch (err) {
      toast.error(enabled ? "Couldn't enable it" : "Couldn't disable it", {
        description: apiErrorMessage(err),
      });
      // A 409 means the page is stale (e.g. config.toml switched it off since): show the lock.
      if (isConflictError(err))
        void queryClient.invalidateQueries({ queryKey: ['asset', assetId] });
    }
  }

  function openMakeGeneric() {
    // A known-different twin skips straight to the replace warning.
    setGenericConflict(data?.generic_twin === 'differs');
    setConfirmGeneric(true);
  }

  async function makeGeneric() {
    try {
      const result = await migrate({ assetId, replaceGeneric: genericConflict });
      setConfirmGeneric(false);
      const where = result.adopted
        ? 'was already in ~/.agents/skills; this copy became a link to it'
        : 'now lives in ~/.agents/skills';
      const kept = result.claude_only_keys.length
        ? ` Kept Claude-only keys: ${result.claude_only_keys.join(', ')}.`
        : '';
      toast.success('Made generic', {
        // Built from who loads it: `linked_agents` never lists Codex, which reads ~/.agents/skills itself.
        description: `${result.asset.title} ${where}; ${loadedByPhrase(result.asset.agents)}.${kept}`,
      });
      navigate(assetDetailPath(kind, result.asset.name, result.asset.provider), { replace: true });
    } catch (err) {
      // A differing generic copy: re-arm into replacing it rather than failing outright.
      if (isConflictError(err) && !genericConflict && /differs/.test(apiErrorMessage(err))) {
        setGenericConflict(true);
        return;
      }
      setConfirmGeneric(false);
      toast.error("Couldn't make it generic", { description: apiErrorMessage(err) });
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

  const toml = isTomlAsset(data.path);
  const { frontmatter, body } = toml
    ? { frontmatter: null, body: data.content }
    : splitFrontmatter(data.content);
  const plugin = isPluginProvider(data.provider);
  // Masterwork never writes ~/.codex/config.toml, so a skill switched off there can't be switched on here.
  const lockedByCodexConfig = data.disabled_by === 'codex-config';
  // Only a skill in one agent's own folder can move to the shared one.
  const canMakeGeneric =
    kind === 'skill' &&
    !data.read_only &&
    (data.provider === 'claude' || data.provider === 'codex');
  const canToggle = kind === 'skill' && !data.read_only && TOGGLEABLE_PROVIDERS.has(data.provider);
  // Catalog installs target any of the three skills folders.
  const mayHaveUpstream = kind === 'skill' && TOGGLEABLE_PROVIDERS.has(data.provider);

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
                title={
                  plugin
                    ? "Plugin assets are managed by their marketplace and can't be edited here."
                    : 'Machine config, managed on disk — read-only here.'
                }
              >
                <Lock className="size-3.5" /> Read-only
                {plugin ? ' · plugin' : null}
              </span>
            ) : mode === 'view' ? (
              <>
                {canToggle ? (
                  <label
                    className="mr-2 inline-flex items-center gap-2 text-sm"
                    title={
                      lockedByCodexConfig
                        ? CODEX_CONFIG_NOTE
                        : 'Off parks the skill under .disabled/ in its folder, where no coding agent looks; nothing is deleted'
                    }
                  >
                    <Switch
                      checked={!data.disabled}
                      onCheckedChange={toggleEnabled}
                      disabled={isToggling || lockedByCodexConfig}
                      aria-label="Enabled"
                    />
                    Enabled
                  </label>
                ) : null}
                {canMakeGeneric ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={openMakeGeneric}
                    title="Move this skill to ~/.agents/skills so every coding agent can use it"
                  >
                    <Share2 /> {data.generic_twin ? 'Merge with generic copy' : 'Make generic'}
                  </Button>
                ) : null}
                <Button size="sm" variant="outline" onClick={startEdit}>
                  <Pencil /> Edit
                </Button>
              </>
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
          <AgentsBadge
            provider={data.provider}
            agents={data.agents}
            disabled={data.disabled}
            disabledBy={data.disabled_by}
          />
          {data.generic_twin ? <GenericTwinBadge twin={data.generic_twin} /> : null}
          <ProviderBadge provider={data.provider} />
          {data.disabled ? <DisabledBadge disabledBy={data.disabled_by} /> : null}
          <ModelBadge model={data.model} showInherit={kind === 'agent'} />
          <code className="font-mono">{shortenPath(data.path)}</code>
          <AssetDatesInline created={data.created_at} updated={data.updated_at} />
        </div>
        {lockedByCodexConfig ? (
          <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
            <Lock className="size-3.5 shrink-0" aria-hidden="true" />
            {CODEX_CONFIG_NOTE}.
          </p>
        ) : null}
        {kind === 'agent' ? <AgentSkillsUsed content={data.content} /> : null}
      </header>

      <AssetChatPanel assetId={assetId} kindLabel={kind === 'skill' ? 'skill' : 'agent'} />

      {mode === 'view' ? (
        <div className="space-y-4">
          {mayHaveUpstream ? <UpstreamCard name={data.name} /> : null}
          <AssetUsageLog assetId={assetId} />
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
          {toml ? (
            // No TOML renderer here, and markdown would mangle it: show the file as written.
            <pre
              aria-label="TOML source"
              className="overflow-x-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-relaxed"
            >
              <code>{data.content}</code>
            </pre>
          ) : (
            <MarkdownView content={body} />
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {saveError ? (
            <p
              role="alert"
              className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>{saveError}</span>
            </p>
          ) : null}
          <CodeEditor
            value={draft}
            onChange={setDraft}
            language={toml ? 'plain' : 'markdown'}
            ariaLabel={toml ? 'TOML editor' : 'Markdown editor'}
            minHeight="30rem"
          />
        </div>
      )}

      <MakeGenericDialog
        open={confirmGeneric}
        name={data.name}
        provider={data.provider}
        conflict={genericConflict}
        pending={isMigrating}
        onConfirm={makeGeneric}
        onCancel={() => setConfirmGeneric(false)}
      />

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
