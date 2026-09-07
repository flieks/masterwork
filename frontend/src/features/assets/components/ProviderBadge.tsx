import { Badge } from '~/components/ui/badge';

/** Display labels per provider id; plugin assets get a distinct outline look. */
const LABELS: Record<string, string> = {
  claude: 'claude',
  'claude-plugin': 'plugin',
  codex: 'codex',
  generic: 'generic',
};

const TITLES: Record<string, string> = {
  claude: 'Files live in ~/.claude/skills or ~/.claude/agents',
  'claude-plugin': 'Provided by an installed plugin (read-only)',
  codex: 'Files live in ~/.codex/skills',
  generic: 'Files live in ~/.agents/skills, the folder every coding agent shares',
};

export function ProviderBadge({ provider }: { provider: string }) {
  const plugin = provider === 'claude-plugin';
  return (
    <Badge
      variant={plugin ? 'outline' : 'secondary'}
      className={`font-mono lowercase ${plugin ? 'border-dashed text-muted-foreground' : ''}`}
      title={TITLES[provider]}
    >
      {LABELS[provider] ?? provider}
    </Badge>
  );
}
