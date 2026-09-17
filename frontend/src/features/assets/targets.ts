import type { SkillTarget } from '~/api/generated';

/** The three skills folders a catalog install can land in. A client-free leaf, like `paths.ts`. */
export const SKILL_TARGETS: { value: SkillTarget; label: string; dir: string }[] = [
  { value: 'claude', label: 'Claude Code', dir: '~/.claude/skills' },
  { value: 'codex', label: 'Codex', dir: '~/.codex/skills' },
  { value: 'generic', label: 'Shared (~/.agents)', dir: '~/.agents/skills' },
];

export function skillTargetDir(target: string): string {
  return SKILL_TARGETS.find((t) => t.value === target)?.dir ?? target;
}

/** "~/.agents/skills, ~/.claude/skills", in the API's order (generic first); null when nowhere. */
export function installedInLabel(installedIn: readonly string[] | undefined): string | null {
  if (!installedIn || installedIn.length === 0) return null;
  return installedIn.map(skillTargetDir).join(', ');
}
