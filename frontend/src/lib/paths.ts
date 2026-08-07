/**
 * Shorten an absolute file path for display.
 * `/Users/me/.claude/skills/frontend-dev/SKILL.md` → `~/.claude/skills/frontend-dev/SKILL.md`.
 */
export function shortenPath(path: string): string {
  if (!path) return '';
  const claudeIdx = path.indexOf('/.claude/');
  if (claudeIdx !== -1) return '~' + path.slice(claudeIdx);
  const homeMatch = path.match(/^\/(?:Users|home)\/[^/]+(\/.*)?$/);
  if (homeMatch) return '~' + (homeMatch[1] ?? '');
  return path;
}
