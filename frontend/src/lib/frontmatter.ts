export interface SplitContent {
  frontmatter: string | null;
  body: string;
}

/** Split a leading YAML frontmatter block (`---\n…\n---`) from the markdown body. */
export function splitFrontmatter(content: string): SplitContent {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return { frontmatter: null, body: content };
  return { frontmatter: match[1], body: content.slice(match[0].length) };
}
