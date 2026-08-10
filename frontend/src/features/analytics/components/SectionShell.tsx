import type { ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { apiErrorMessage } from '~/api/client';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';

/**
 * One aggregate, with its loading, error and empty states handled the same way
 * as the other three. The four sections stack on one page under one filter bar,
 * so any difference in how they fail would read as a difference in the data.
 */
export function SectionShell({
  title,
  description,
  count,
  isPending,
  isError,
  error,
  onRetry,
  empty,
  children,
}: {
  title: string;
  description: string;
  count?: string;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  /** Rendered instead of the children when the aggregate came back empty. */
  empty?: ReactNode;
  children: ReactNode;
}) {
  return (
    // `min-w-0`: a flex item defaults to min-width:auto, which lets a wide
    // table push past its own `overflow-x-auto` wrapper and scroll the page.
    <section className="flex min-w-0 flex-col gap-3">
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          {count ? <span className="font-mono text-xs text-muted-foreground">{count}</span> : null}
        </div>
        <p className="max-w-3xl text-xs leading-relaxed text-muted-foreground">{description}</p>
      </header>

      {isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title={`Couldn't load ${title.toLowerCase()}`}
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={onRetry}>
              Retry
            </Button>
          }
        />
      ) : (
        (empty ?? children)
      )}
    </section>
  );
}
