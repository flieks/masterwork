import { useAtom } from 'jotai';
import { LayoutGrid, Table2 } from 'lucide-react';
import { assetViewAtom, type AssetView } from '../atoms';
import { cn } from '~/lib/utils';

const options: { value: AssetView; label: string; icon: typeof LayoutGrid }[] = [
  { value: 'grid', label: 'Grid view', icon: LayoutGrid },
  { value: 'table', label: 'Table view', icon: Table2 },
];

export function ViewToggle() {
  const [view, setView] = useAtom(assetViewAtom);

  return (
    <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="View mode">
      {options.map(({ value, label, icon: Icon }) => {
        const active = view === value;
        return (
          <button
            key={value}
            type="button"
            aria-label={label}
            aria-pressed={active}
            onClick={() => setView(value)}
            className={cn(
              'inline-flex h-7 w-7 items-center justify-center rounded-sm transition-colors',
              active
                ? 'bg-secondary text-secondary-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-4" />
          </button>
        );
      })}
    </div>
  );
}
