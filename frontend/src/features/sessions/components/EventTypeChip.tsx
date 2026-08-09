import { Badge } from '~/components/ui/badge';
import { cn } from '~/lib/utils';

// Hook names are a free string in the contract, so this is a lookup with a
// neutral fallback — a hook type that doesn't exist yet still renders.
const EVENT_STYLES: Record<string, string> = {
  SessionStart: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  UserPromptSubmit: 'bg-blue-500/15 text-blue-700 dark:text-blue-400',
  PostToolUse: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
  SubagentStop: 'bg-violet-500/15 text-violet-700 dark:text-violet-400',
  Stop: 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
  SessionEnd: 'bg-rose-500/15 text-rose-700 dark:text-rose-400',
};

export function EventTypeChip({ eventType }: { eventType: string }) {
  const style: string | undefined = EVENT_STYLES[eventType];
  return (
    <Badge
      variant={style ? 'default' : 'muted'}
      className={cn('border-transparent font-mono text-[11px]', style)}
    >
      {eventType}
    </Badge>
  );
}
