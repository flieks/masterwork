import { useState } from 'react';
import { useAtom, useAtomValue } from 'jotai';
import { useSearchParams } from 'react-router-dom';
import { Search, PackageOpen, AlertTriangle } from 'lucide-react';
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
import { AssetGrid } from './AssetGrid';
import { AssetTable } from './AssetTable';
import { AssetListSkeleton } from './AssetListSkeleton';
import { SkillCatalog } from './SkillCatalog';

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
        <ViewToggle />
      </div>

      {isPending ? (
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
      ) : data.length === 0 ? (
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
              : `Install ${copy.noun} under ~/.claude${kind === 'skill' ? ', ~/.codex or ~/.agents' : ''} to see them here.`
          }
        />
      ) : view === 'grid' ? (
        <AssetGrid kind={kind} assets={data} />
      ) : (
        <AssetTable kind={kind} assets={data} />
      )}
    </div>
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">{copy.title}</h1>
          {tab === 'installed' && data ? (
            <Badge variant="muted" aria-label={`${data.length} ${copy.noun}`}>
              {data.length}
            </Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          Globally installed {copy.noun}
          {kind === 'skill'
            ? ' across Claude Code, Codex and the shared ~/.agents folder'
            : ' under ~/.claude'}
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
