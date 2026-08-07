import { Cpu } from 'lucide-react';
import { Badge } from '~/components/ui/badge';

interface ModelBadgeProps {
  /** Frontmatter `model:`; null/undefined means no override. */
  model?: string | null;
  /** Render an "inherit" badge when there is no override instead of nothing. */
  showInherit?: boolean;
  /** Shorter label, for tight spots like list cards. */
  compact?: boolean;
}

export function ModelBadge({ model, showInherit = false, compact = false }: ModelBadgeProps) {
  if (!model) {
    if (!showInherit) return null;
    return (
      <Badge
        variant="outline"
        className="gap-1 border-dashed font-mono text-muted-foreground"
        title="No model set in frontmatter — runs on whatever model the session uses."
      >
        <Cpu className="size-3" /> {compact ? 'inherits' : 'inherits session model'}
      </Badge>
    );
  }
  return (
    <Badge
      variant="secondary"
      className="gap-1 font-mono lowercase"
      title={`Frontmatter model: ${model}`}
    >
      <Cpu className="size-3" /> {model}
    </Badge>
  );
}
