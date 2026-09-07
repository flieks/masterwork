import { useState } from 'react';
import { useAtom } from 'jotai';
import { Input } from '~/components/ui/input';
import { Button } from '~/components/ui/button';
import { toast } from '~/components/ui/sonner';
import { apiErrorMessage } from '~/api/client';
import { createWorkSourceMutationAtom, DEFAULT_SECRET_REF } from '../queries';

/** First-run form: registers the Azure DevOps project a backlog is pulled from. */
export function NewWorkSourceForm() {
  const [{ mutateAsync: create, isPending }] = useAtom(createWorkSourceMutationAtom);

  const [orgUrl, setOrgUrl] = useState('');
  const [project, setProject] = useState('');
  const [team, setTeam] = useState('');
  const [secretRef, setSecretRef] = useState(DEFAULT_SECRET_REF);

  const canCreate =
    orgUrl.trim().length > 0 &&
    project.trim().length > 0 &&
    secretRef.trim().length > 0 &&
    !isPending;

  async function submit() {
    if (!canCreate) return;
    try {
      await create({
        org_url: orgUrl.trim(),
        project: project.trim(),
        team: team.trim() || undefined,
        secret_ref: secretRef.trim(),
      });
      setOrgUrl('');
      setProject('');
      setTeam('');
      setSecretRef(DEFAULT_SECRET_REF);
      toast.success('Work source added', { description: 'Sync it to pull the backlog in.' });
    } catch (err) {
      toast.error('Could not add the work source', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="rounded-lg border border-dashed p-6">
      <div className="space-y-1">
        <p className="text-sm font-medium">No work source yet</p>
        <p className="text-sm text-muted-foreground">
          Point masterwork at an Azure DevOps project. Reads only — nothing is ever written back.
        </p>
      </div>

      <form
        className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="space-y-1.5">
          <label htmlFor="work-org-url" className="text-sm font-medium">
            Organisation URL
          </label>
          <Input
            id="work-org-url"
            value={orgUrl}
            onChange={(e) => setOrgUrl(e.target.value)}
            placeholder="https://dev.azure.com/myorg"
            autoFocus
            required
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="work-project" className="text-sm font-medium">
            Project
          </label>
          <Input
            id="work-project"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            placeholder="MyProject"
            required
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="work-team" className="text-sm font-medium">
            Team <span className="font-normal text-muted-foreground">(optional)</span>
          </label>
          <Input
            id="work-team"
            value={team}
            onChange={(e) => setTeam(e.target.value)}
            placeholder="Leave empty for the project default"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="work-secret-ref" className="text-sm font-medium">
            PAT env var
          </label>
          <Input
            id="work-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            required
          />
          <p className="text-xs text-muted-foreground">
            The variable name only — the token itself stays in the backend&apos;s environment.
          </p>
        </div>

        <div className="sm:col-span-2">
          <Button type="submit" disabled={!canCreate}>
            {isPending ? 'Adding…' : 'Add work source'}
          </Button>
        </div>
      </form>
    </div>
  );
}
