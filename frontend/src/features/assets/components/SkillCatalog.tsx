import { useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, PackageSearch, Search } from 'lucide-react';
import type { CatalogSkill } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { useDebouncedValue } from '~/lib/hooks';
import { skillCatalogQueryAtom } from '../queries';
import { LicenseBadge } from './LicenseBadge';
import { SkillPreviewDialog } from './SkillPreviewDialog';

const REGISTRY_LABEL: Record<string, string> = {
  skills_sh: 'skills.sh',
  github: 'GitHub',
};

/** Browse and preview the community skill catalog — the other half of the Assets surface. */
export function SkillCatalog() {
  const [rawQuery, setRawQuery] = useState('');
  const debouncedQuery = useDebouncedValue(rawQuery, 300);
  const query = debouncedQuery.trim();
  const [preview, setPreview] = useState<CatalogSkill | null>(null);

  const [{ data, isPending, isError, error, refetch }] = useAtom(skillCatalogQueryAtom(query));

  return (
    <div className="flex flex-col gap-4">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          aria-label="Search the skill catalog"
          placeholder="Search community skills by name…"
          value={rawQuery}
          onChange={(e) => setRawQuery(e.target.value)}
          className="pl-9"
        />
      </div>

      {query === '' ? (
        <EmptyState
          icon={<PackageSearch className="size-8" />}
          title="Search the community catalog"
          description="Results come from skills.sh and GitHub — nothing is installed until you choose to."
        />
      ) : isPending ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't search the catalog"
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : data.skills.length === 0 ? (
        <EmptyState
          icon={<PackageSearch className="size-8" />}
          title={`No matches for "${query}"`}
          description="Try a different search term."
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {data.skills.map((skill) => (
            <li key={`${skill.owner}/${skill.repo}/${skill.skill}`}>
              <button
                type="button"
                onClick={() => setPreview(skill)}
                className="flex w-full flex-col gap-1 rounded-lg border bg-card p-3 text-left hover:bg-accent"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  {/* Every registry-supplied string renders as plain text — never HTML/markdown. */}
                  <span className="font-medium">{skill.name}</span>
                  <Badge variant="muted">{REGISTRY_LABEL[skill.registry] ?? skill.registry}</Badge>
                  <LicenseBadge license={skill.license} resolved={skill.license_resolved} />
                  {skill.installed ? <Badge variant="outline">Installed</Badge> : null}
                  {skill.registry === 'skills_sh' && skill.installs !== null ? (
                    <span className="text-xs text-muted-foreground">{skill.installs} installs</span>
                  ) : null}
                </div>
                <p className="truncate text-xs text-muted-foreground">
                  {skill.owner}/{skill.repo}
                </p>
                {skill.description ? (
                  <p className="truncate text-sm text-muted-foreground">{skill.description}</p>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}

      {data && data.errors.length > 0 ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="size-3.5 shrink-0" />
          {data.errors
            .map((e) => `${REGISTRY_LABEL[e.registry] ?? e.registry} results unavailable: ${e.message}`)
            .join(' ')}
        </p>
      ) : null}

      <SkillPreviewDialog skill={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
