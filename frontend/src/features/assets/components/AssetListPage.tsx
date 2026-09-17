import { useMemo, useState } from 'react';
import { useAtom, useAtomValue } from 'jotai';
import { useSearchParams } from 'react-router-dom';
import { Search, PackageOpen, AlertTriangle, X } from 'lucide-react';
import type { SkillMatch } from '~/api/generated';
import { Input } from '~/components/ui/input';
import { Button } from '~/components/ui/button';
import { Badge } from '~/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { EmptyState } from '~/components/EmptyState';
import { useDebouncedValue } from '~/lib/hooks';
import { apiErrorMessage } from '~/api/client';
import { assetViewAtom } from '../atoms';
import { assetsQueryAtom, assetsListKey, type AssetKind } from '../queries';
import { ViewToggle } from './ViewToggle';
import { hideMergedTwins } from '../duplicates';
import { AssetGrid } from './AssetGrid';
import { AssetTable } from './AssetTable';
import { AssetListSkeleton } from './AssetListSkeleton';
import { SkillCatalog } from './SkillCatalog';
import { SkillMatchPanel } from './SkillMatchPanel';

const COPY: Record<AssetKind, { title: string; noun: string; placeholder: string }> = {
  skill: {
    title: 'Skills',
    noun: 'skills',
    placeholder: 'Search skills by name, description, or content…',
  },
  agent: {
    title: 'Agents',
    noun: 'agents',
    placeholder: 'Search agents by name, description, or content…',
  },
};

// Only 'skill' pages have a catalog tab — /agents has no community catalog.
const ASSET_TABS = ['installed', 'catalog'] as const;
type AssetTab = (typeof ASSET_TABS)[number];

function isAssetTab(value: string | null): value is AssetTab {
  return ASSET_TABS.includes(value as AssetTab);
}

export function AssetListPage({ kind }: { kind: AssetKind }) {
  const copy = COPY[kind];
  const [params, setParams] = useSearchParams();
  const tab: AssetTab =
    kind === 'skill' && isAssetTab(params.get('view'))
      ? (params.get('view') as AssetTab)
      : 'installed';

  const [rawQuery, setRawQuery] = useState('');
  const debouncedQuery = useDebouncedValue(rawQuery, 300);
  const view = useAtomValue(assetViewAtom);

  const [{ data, isPending, isError, error, refetch }] = useAtom(
    assetsQueryAtom(assetsListKey(kind, debouncedQuery.trim())),
  );
  const visible = useMemo(() => hideMergedTwins(data ?? []), [data]);

  // The describe-to-find match source: the unfiltered list, so a leftover
  // search term never hides a skill the model matched.
  const [{ data: unfilteredData }] = useAtom(assetsQueryAtom(assetsListKey(kind, '')));
  const [showMatchPanel, setShowMatchPanel] = useState(false);
  const [matches, setMatches] = useState<SkillMatch[] | null>(null);
  const showingMatches = kind === 'skill' && matches !== null;

  const matchedAssets = useMemo(() => {
    if (!matches || !unfilteredData) return null;
    const byName = new Map(hideMergedTwins(unfilteredData).map((a) => [a.name, a]));
    return matches.flatMap((m) => {
      const asset = byName.get(m.name);
      return asset ? [asset] : [];
    });
  }, [matches, unfilteredData]);

  const captions = useMemo(
    () => (matches ? new Map(matches.map((m) => [m.name, m.reason])) : undefined),
    [matches],
  );

  const installedList = (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            aria-label={`Search ${copy.noun}`}
            placeholder={copy.placeholder}
            value={rawQuery}
            onChange={(e) => setRawQuery(e.target.value)}
            className="pl-9"
          />
        </div>
        {kind === 'skill' ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowMatchPanel((open) => !open)}
            aria-pressed={showMatchPanel}
          >
            Find by description
          </Button>
        ) : null}
        <ViewToggle />
      </div>

      {showMatchPanel ? <SkillMatchPanel onMatches={setMatches} /> : null}

      {showingMatches ? (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            {matchedAssets?.length ?? 0} matching {copy.noun}
          </p>
          <Button variant="ghost" size="sm" onClick={() => setMatches(null)}>
            <X className="size-3.5" />
            Clear
          </Button>
        </div>
      ) : null}

      {showingMatches ? (
        matchedAssets === null ? (
          <AssetListSkeleton view={view} />
        ) : matchedAssets.length === 0 ? (
          <EmptyState
            icon={<PackageOpen className="size-8" />}
            title="No matching skills"
            description="Try describing what you need differently."
          />
        ) : view === 'grid' ? (
          <AssetGrid kind={kind} assets={matchedAssets} captions={captions} />
        ) : (
          <AssetTable kind={kind} assets={matchedAssets} captions={captions} />
        )
      ) : isPending ? (
        <AssetListSkeleton view={view} />
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load assets"
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<PackageOpen className="size-8" />}
          title={
            debouncedQuery.trim()
              ? `No matches for "${debouncedQuery.trim()}"`
              : `No ${copy.noun} installed`
          }
          description={
            debouncedQuery.trim()
              ? 'Try a different search term.'
              : kind === 'skill'
                ? `Install ${copy.noun} under ~/.claude, ~/.codex or ~/.agents to see them here.`
                : `Install ${copy.noun} under ~/.claude/agents or ~/.codex/agents to see them here.`
          }
        />
      ) : view === 'grid' ? (
        <AssetGrid kind={kind} assets={visible} />
      ) : (
        <AssetTable kind={kind} assets={visible} />
      )}
    </div>
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{copy.title}</h1>
          {tab === 'installed' && data ? (
            <Badge variant="muted" aria-label={`${visible.length} ${copy.noun}`}>
              {visible.length}
            </Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          Globally installed {copy.noun}
          {kind === 'skill'
            ? ' across Claude Code, Codex and the shared ~/.agents folder'
            : ' across Claude Code (~/.claude/agents) and Codex (~/.codex/agents)'}
          . Search matches title, description, and file content.
        </p>
      </header>

      {kind === 'skill' ? (
        <Tabs
          value={tab}
          onValueChange={(next) =>
            setParams(next === 'installed' ? {} : { view: next }, { replace: true })
          }
          className="flex flex-col gap-4"
        >
          <TabsList className="self-start">
            <TabsTrigger value="installed">Installed</TabsTrigger>
            <TabsTrigger value="catalog">Catalog</TabsTrigger>
          </TabsList>

          <TabsContent value="installed">{installedList}</TabsContent>
          <TabsContent value="catalog">
            <SkillCatalog />
          </TabsContent>
        </Tabs>
      ) : (
        installedList
      )}
    </div>
  );
}
