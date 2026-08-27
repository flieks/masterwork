import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { ExternalLink } from 'lucide-react';
import type { CatalogSkill } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { MarkdownView } from '~/components/MarkdownView';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { Skeleton } from '~/components/ui/skeleton';
import { toast } from '~/components/ui/sonner';
import { absoluteDate, relativeTime } from '~/lib/datetime';
import { catalogSkillKey, catalogSkillQueryAtom, installSkillMutationAtom } from '../queries';
import { LicenseBadge } from './LicenseBadge';

interface SkillPreviewDialogProps {
  skill: CatalogSkill | null;
  onClose: () => void;
}


/** Split the leading `---` YAML block off the body. Delimiter scan only — the
 *  backend already parses the YAML; this just keeps it out of the rendered
 *  markdown, where `---` would come out as a stray rule. */
function splitFrontmatter(md: string): { frontmatter: string | null; body: string } {
  if (!md.startsWith('---\n')) return { frontmatter: null, body: md };
  const end = md.indexOf('\n---', 3);
  if (end === -1) return { frontmatter: null, body: md };
  return {
    frontmatter: md.slice(4, end).trim(),
    body: md.slice(end + 4).replace(/^\n+/, ''),
  };
}

/** SKILL.md preview before install — rendered as markdown.
 *  Safe for third-party content: MarkdownView has no rehype-raw, so embedded HTML
 *  is escaped rather than run, and its mermaid renderer is securityLevel 'strict'. */
export function SkillPreviewDialog({ skill, onClose }: SkillPreviewDialogProps) {
  const key = skill ? catalogSkillKey(skill.owner, skill.repo, skill.skill) : catalogSkillKey('', '', '');
  const [{ data, isPending, isError, error }] = useAtom(catalogSkillQueryAtom(key));
  const [{ mutateAsync: install, isPending: installing }] = useAtom(installSkillMutationAtom);
  // An unlicensed skill, and a reinstall over an existing directory, each need a
  // second, risk-naming click before anything is written.
  const [confirmingRisk, setConfirmingRisk] = useState(false);

  useEffect(() => {
    setConfirmingRisk(false);
  }, [skill]);

  if (!skill) return null;
  const active = skill; // narrowing does not survive into the closure below

  async function doInstall() {
    try {
      await install({
        owner: active.owner,
        repo: active.repo,
        skill: active.skill,
        overwrite: data?.installed ?? false,
      });
      toast.success(`Installed ${active.name}`);
      onClose();
    } catch (err) {
      toast.error('Could not install this skill', { description: apiErrorMessage(err) });
    }
  }

  function handleInstallClick() {
    if ((data?.all_rights_reserved || data?.installed) && !confirmingRisk) {
      setConfirmingRisk(true);
      return;
    }
    void doInstall();
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden">
        <DialogHeader>
          {/* Registry-supplied name — plain text only. */}
          <DialogTitle>{skill.name}</DialogTitle>
          <DialogDescription>
            {data?.url ? (
              <a
                href={data.url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
              >
                {skill.owner}/{skill.repo}
                <ExternalLink className="size-3" />
              </a>
            ) : (
              `${skill.owner}/${skill.repo}`
            )}
          </DialogDescription>
        </DialogHeader>

        {isPending ? (
          <Skeleton className="h-48 w-full" />
        ) : isError ? (
          <p className="text-sm text-destructive">{apiErrorMessage(error)}</p>
        ) : (
          <div className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto">
            <div className="flex flex-wrap items-center gap-2">
              <LicenseBadge license={data.license} />
              {data.version ? <Badge variant="secondary">v{data.version}</Badge> : null}
              {data.installed ? (
                <Badge variant="outline">
                  {data.installed_by_masterwork ? 'Installed' : 'Already on disk'}
                  {data.installed_version ? ` · v${data.installed_version}` : ''}
                </Badge>
              ) : null}
              {data.differs_from_installed === true ? (
                <Badge variant="destructive">Your copy differs</Badge>
              ) : data.differs_from_installed === false ? (
                <Badge variant="muted">Matches your copy</Badge>
              ) : null}
              {data.files.length > 0 ? (
                <span className="text-xs text-muted-foreground">
                  {data.files.length} companion file{data.files.length === 1 ? '' : 's'}
                </span>
              ) : null}
            </div>
            {data.created_at || data.last_modified_at ? (
              <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
                {data.created_at ? (
                  <div className="flex gap-1.5">
                    <dt>Added</dt>
                    <dd className="text-foreground">{absoluteDate(data.created_at)}</dd>
                  </div>
                ) : null}
                {data.last_modified_at ? (
                  <div className="flex gap-1.5">
                    <dt>Last changed</dt>
                    <dd className="text-foreground">
                      {absoluteDate(data.last_modified_at)}
                      <span className="text-muted-foreground">
                        {' '}
                        ({relativeTime(data.last_modified_at)})
                      </span>
                    </dd>
                  </div>
                ) : null}
                {/* Commit subject from the source repo — plain text only. */}
                {data.last_change_summary ? (
                  <div className="flex w-full min-w-0 gap-1.5">
                    <dt className="shrink-0">Latest commit</dt>
                    <dd className="min-w-0 truncate text-foreground" title={data.last_change_summary}>
                      {data.last_change_summary}
                    </dd>
                  </div>
                ) : null}
              </dl>
            ) : null}
            {(() => {
              const { frontmatter, body } = splitFrontmatter(data.skill_md);
              return (
                <>
                  {/* Kept verbatim: allowed-tools belongs in front of an install decision. */}
                  {frontmatter ? (
                    <pre className="w-full min-w-0 shrink-0 overflow-x-auto whitespace-pre-wrap [overflow-wrap:anywhere] rounded-md border bg-muted/40 p-3 text-xs text-muted-foreground">
                      {frontmatter}
                    </pre>
                  ) : null}
                  <MarkdownView
                    content={body}
                    className="w-full min-w-0 shrink-0 [overflow-wrap:anywhere] text-sm [&_li]:max-w-[85ch] [&_p]:max-w-[85ch]"
                  />
                </>
              );
            })()}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={confirmingRisk ? 'destructive' : 'default'}
            disabled={isPending || isError || installing}
            onClick={handleInstallClick}
          >
            {installing
              ? 'Installing…'
              : confirmingRisk
                ? data?.installed
                  ? 'Replace the copy already on disk'
                  : 'Install anyway — no license grants you the right to use this'
                : data?.installed
                  ? 'Reinstall'
                  : 'Install'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
