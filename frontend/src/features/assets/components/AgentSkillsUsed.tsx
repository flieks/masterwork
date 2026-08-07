import { Link } from 'react-router-dom';
import { useAtom } from 'jotai';
import { badgeVariants } from '~/components/ui/badge';
import { cn } from '~/lib/utils';
import { assetsListKey, assetsQueryAtom, assetDetailPath } from '../queries';

/**
 * True when the agent's markdown references this skill name: backticked
 * references always count; bare tokens only for hyphen/colon slugs, since
 * single-word names ("qa", "implement") false-positive in prose.
 */
function referencesSkill(content: string, name: string): boolean {
  if (content.includes(`\`${name}\``)) return true;
  if (!/[-:]/.test(name)) return false;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![\\w-])${escaped}(?![\\w-])`).test(content);
}

/** Skills the agent's file mentions, as badge links. Renders nothing when none match. */
export function AgentSkillsUsed({ content }: { content: string }) {
  const [{ data: skills }] = useAtom(assetsQueryAtom(assetsListKey('skill', '')));
  const used = (skills ?? []).filter((s) => referencesSkill(content, s.name));
  if (used.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span className="font-medium">Uses skills:</span>
      {used.map((s) => (
        <Link
          key={s.id}
          to={assetDetailPath('skill', s.name, s.provider)}
          title={s.description || undefined}
          className={cn(badgeVariants({ variant: 'secondary' }), 'font-mono hover:bg-secondary/70')}
        >
          {s.name}
        </Link>
      ))}
    </div>
  );
}
