import { cn } from '~/lib/utils';

interface UnifiedDiffViewProps {
  /** A unified diff as text, headers included. */
  diff: string;
  label?: string;
  className?: string;
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'font-medium text-muted-foreground';
  if (line.startsWith('@@')) return 'text-sky-700 dark:text-sky-400';
  if (line.startsWith('+')) return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400';
  if (line.startsWith('-')) return 'bg-red-500/10 text-red-700 dark:text-red-400';
  return 'text-foreground/80';
}

/** Renders a unified diff with the usual add/remove colouring — plain text
 *  only, so third-party file content never becomes markup. */
export function UnifiedDiffView({ diff, label = 'Diff', className }: UnifiedDiffViewProps) {
  const lines = diff.replace(/\n$/, '').split('\n');
  return (
    <pre
      aria-label={label}
      className={cn(
        'overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-5',
        className,
      )}
    >
      {lines.map((line, i) => (
        <div key={i} className={cn('whitespace-pre px-1', lineClass(line))}>
          {line || ' '}
        </div>
      ))}
    </pre>
  );
}
