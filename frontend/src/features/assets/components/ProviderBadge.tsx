import { Badge } from '~/components/ui/badge';

/** Display labels per provider id; plugin assets get a distinct outline look. */
const LABELS: Record<string, string> = {
  claude: 'claude',
  'claude-plugin': 'plugin',
};

export function ProviderBadge({ provider }: { provider: string }) {
  const plugin = provider === 'claude-plugin';
  return (
    <Badge
      variant={plugin ? 'outline' : 'secondary'}
      className={`font-mono lowercase ${plugin ? 'border-dashed text-muted-foreground' : ''}`}
      title={plugin ? 'Provided by an installed plugin (read-only)' : undefined}
    >
      {LABELS[provider] ?? provider}
    </Badge>
  );
}
