/** A hook payload or an envelope body can be 32 KB — cap what we put in the DOM. */
export const MAX_JSON_CHARS = 4000;

/** Pretty-printed JSON, truncated for display; null when there is nothing to show. */
export function formatJson(
  value: Record<string, unknown> | null,
  max = MAX_JSON_CHARS,
): string | null {
  if (!value || Object.keys(value).length === 0) return null;
  let text: string;
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    return null;
  }
  return truncateForDisplay(text, max);
}

/** The same cap for text that was never JSON — a raw model reply, say. */
export function truncateForDisplay(text: string, max = MAX_JSON_CHARS): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}\n… truncated (${text.length.toLocaleString()} characters)`;
}
