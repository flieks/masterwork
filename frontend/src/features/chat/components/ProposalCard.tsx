import type { ReactNode } from 'react';
import { useAtom } from 'jotai';
import { useQueryClient } from '@tanstack/react-query';
import { Check, X, FileDiff, FolderCog } from 'lucide-react';
import type {
  ChatMessage,
  Proposal,
  ProposalChange,
  ProposalStatus,
  ProjectUpdate,
} from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { CodeEditor } from '~/components/CodeEditor';
import { MarkdownView } from '~/components/MarkdownView';
import { MermaidView } from '~/components/MermaidView';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { shortenPath } from '~/lib/paths';
import { acceptProposalMutationAtom, rejectProposalMutationAtom } from '../queries';

const STATUS_META: Record<
  ProposalStatus,
  { label: string; variant: 'secondary' | 'success' | 'muted' | 'destructive' }
> = {
  pending: { label: 'Pending', variant: 'secondary' },
  applied: { label: 'Applied ✓', variant: 'success' },
  rejected: { label: 'Rejected', variant: 'muted' },
  failed: { label: 'Failed', variant: 'destructive' },
};

const ACTION_VARIANT: Record<ProposalChange['action'], 'success' | 'secondary' | 'destructive'> = {
  create: 'success',
  update: 'secondary',
  delete: 'destructive',
};

interface ProposalCardProps {
  proposal: Proposal;
  sessionId: string;
}

export function ProposalCard({ proposal, sessionId }: ProposalCardProps) {
  const [{ mutateAsync: accept, isPending: accepting }] = useAtom(acceptProposalMutationAtom);
  const [{ mutateAsync: reject, isPending: rejecting }] = useAtom(rejectProposalMutationAtom);
  const queryClient = useQueryClient();

  const busy = accepting || rejecting;
  // Failed proposals stay actionable: accept retries, reject dismisses.
  const failed = proposal.status === 'failed';
  const actionable = proposal.status === 'pending' || failed;
  // An update/create without content can never apply — retrying is pointless.
  const unapplyable = proposal.changes.some((c) => c.action !== 'delete' && c.new_content == null);
  const statusMeta = STATUS_META[proposal.status];

  function applyUpdated(updated: Proposal) {
    // Reflect the new status on the proposal embedded in the cached message.
    queryClient.setQueryData<ChatMessage[]>(['chatMessages', sessionId], (old) =>
      old?.map((m) => (m.proposal?.id === updated.id ? { ...m, proposal: updated } : m)),
    );
  }

  async function onAccept() {
    try {
      const updated = await accept(proposal.id);
      applyUpdated(updated);
      if (updated.status === 'applied') {
        // Edited files changed on disk — refresh asset lists and any open detail.
        queryClient.invalidateQueries({ queryKey: ['assets'] });
        for (const change of updated.changes) {
          if (change.asset_id) {
            queryClient.invalidateQueries({ queryKey: ['asset', change.asset_id] });
          }
        }
        // A project update was applied — refresh the project overview + list.
        if (updated.project_update) {
          queryClient.invalidateQueries({
            queryKey: ['project', updated.project_update.project_id],
          });
          queryClient.invalidateQueries({ queryKey: ['projects'] });
        }
        toast.success('Changes applied');
      } else if (updated.status === 'failed') {
        toast.error('Apply failed', { description: updated.error ?? undefined });
      }
    } catch (err) {
      toast.error('Accept failed', { description: apiErrorMessage(err) });
    }
  }

  async function onReject() {
    try {
      applyUpdated(await reject(proposal.id));
      toast('Proposal rejected');
    } catch (err) {
      toast.error('Reject failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="mt-3 overflow-hidden rounded-lg border bg-card">
      <div className="flex items-start justify-between gap-3 border-b bg-muted/40 px-3 py-2.5">
        <div className="flex min-w-0 items-start gap-2">
          <FileDiff className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <p className="text-sm font-medium">Proposed changes</p>
            <p className="text-sm text-muted-foreground">{proposal.summary}</p>
          </div>
        </div>
        <Badge variant={statusMeta.variant} className="shrink-0">
          {statusMeta.label}
        </Badge>
      </div>

      {proposal.project_update ? <ProjectUpdateBlock update={proposal.project_update} /> : null}

      {proposal.changes.length > 0 ? (
        <ul className="divide-y">
          {proposal.changes.map((change, i) => (
            <li key={`${change.path}-${i}`} className="px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Badge variant={ACTION_VARIANT[change.action]} className="uppercase">
                  {change.action}
                </Badge>
                <code
                  className="truncate font-mono text-xs text-muted-foreground"
                  title={change.path}
                >
                  {shortenPath(change.path)}
                </code>
              </div>
              {change.description ? (
                <p className="mt-1 text-sm text-muted-foreground">{change.description}</p>
              ) : null}
              {change.new_content != null ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
                    {change.action === 'create' ? 'View file content' : 'View new content'}
                  </summary>
                  <div className="mt-2">
                    <CodeEditor
                      value={change.new_content}
                      readOnly
                      minHeight="4rem"
                      maxHeight="24rem"
                      ariaLabel={`New content for ${change.path}`}
                    />
                  </div>
                </details>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex items-center justify-between gap-3 border-t px-3 py-2.5">
        {proposal.status === 'failed' && proposal.error ? (
          <p className="text-sm text-destructive">{proposal.error}</p>
        ) : proposal.changes.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {proposal.changes.length} file{proposal.changes.length === 1 ? '' : 's'}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">Project update</span>
        )}
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="outline" onClick={onReject} disabled={!actionable || busy}>
            <X /> Reject
          </Button>
          {failed && unapplyable ? null : (
            <Button size="sm" onClick={onAccept} disabled={!actionable || busy}>
              <Check /> {accepting ? 'Applying…' : failed ? 'Retry' : 'Accept'}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** The project-update portion of a proposal: only the non-null fields render. */
function ProjectUpdateBlock({ update }: { update: ProjectUpdate }) {
  return (
    <section className="border-b bg-muted/20 px-3 py-2.5" aria-label="Project update">
      <div className="flex items-center gap-2">
        <FolderCog className="size-4 shrink-0 text-muted-foreground" />
        <p className="text-sm font-medium">Project update</p>
      </div>
      {update.description ? (
        <p className="mt-1 text-sm text-muted-foreground">{update.description}</p>
      ) : null}

      <div className="mt-2 space-y-2.5">
        {update.name != null ? (
          <Field label="Name">
            <span className="text-sm">{update.name}</span>
          </Field>
        ) : null}

        {update.goal != null ? (
          <Field label="Goal">
            <details className="rounded-md border bg-background">
              <summary className="cursor-pointer px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                View goal
              </summary>
              <div className="border-t px-2.5 py-2">
                <MarkdownView content={update.goal} />
              </div>
            </details>
          </Field>
        ) : null}

        {update.asset_ids != null ? (
          <Field label="Linked assets">
            {update.asset_ids.length === 0 ? (
              <span className="text-sm italic text-muted-foreground">none</span>
            ) : (
              <div className="flex flex-wrap gap-1">
                {update.asset_ids.map((id) => (
                  <Badge key={id} variant="secondary" className="font-mono text-[11px]">
                    {id}
                  </Badge>
                ))}
              </div>
            )}
          </Field>
        ) : null}

        {update.flow_mermaid != null ? (
          <Field label="Flow diagram">
            <details className="rounded-md border bg-background">
              <summary className="cursor-pointer px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                Preview diagram
              </summary>
              <div className="border-t px-2.5 py-2">
                <MermaidView source={update.flow_mermaid} />
              </div>
            </details>
          </Field>
        ) : null}
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}
