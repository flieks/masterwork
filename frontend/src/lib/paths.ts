/** Agent homes shown from `~` even when the home dir isn't under /Users or /home. */
const AGENT_DIRS = ['/.claude/', '/.codex/', '/.agents/'];

/**
 * Shorten an absolute file path for display.
 * `/Users/me/.claude/skills/frontend-dev/SKILL.md` → `~/.claude/skills/frontend-dev/SKILL.md`.
 */
export function shortenPath(path: string): string {
  if (!path) return '';
  const hits = AGENT_DIRS.map((dir) => path.indexOf(dir)).filter((idx) => idx !== -1);
  if (hits.length > 0) return '~' + path.slice(Math.min(...hits));
  const homeMatch = path.match(/^\/(?:Users|home)\/[^/]+(\/.*)?$/);
  if (homeMatch) return '~' + (homeMatch[1] ?? '');
  return path;
}
