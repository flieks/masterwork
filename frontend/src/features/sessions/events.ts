import type { CodingEvent } from '~/api/generated';
import { shortenPath } from '~/lib/paths';

/**
 * What a tool call was actually about, pulled out of `tool_input`.
 *
 * A stream of `PostToolUse · Bash` rows says nothing until you expand each one;
 * the argument a human would have named the call by — the command's own
 * description, the file that was read — is one key down in the payload. This
 * lifts it into the row, so the timeline reads the way the tool call did.
 */

/** Long enough for a command, short enough to keep the row one line. */
const MAX_SUMMARY_CHARS = 120;

/** Most human-readable key first: a description beats the command it describes. */
const SUMMARY_KEYS = [
  'description',
  'file_path',
  'notebook_path',
  'command',
  'pattern',
  'url',
  'query',
  'skill',
  'subagent_type',
  'prompt',
  'path',
];

/** Keys whose value is a path: shown by file name, titled by full path. */
const PATH_KEYS = new Set(['file_path', 'notebook_path', 'path']);

/**
 * The hook a tool call arrives on, when it is worth naming.
 *
 * Every tool event in the stream today is a `PostToolUse`, so the chip repeats
 * on every row and distinguishes nothing. The ones that are not — `PreToolUse`,
 * which is the only hook that knows when a subagent *started*, and the factory's
 * own `tool_call` — say something, and keep their chip.
 */
const IMPLIED_TOOL_HOOK = 'PostToolUse';

export function showsEventType(event: CodingEvent): boolean {
  return event.tool_name === null || event.event_type !== IMPLIED_TOOL_HOOK;
}

/**
 * Tools whose every use is worth its own row. A run of `Read`s is one act of
 * reading and collapses to a count; which skills a run loaded is the point of
 * the screen, so those never fold into "Skill ×4".
 */
const NEVER_GROUPED = new Set(['Skill']);

/** Two in a row is already a run — one row saying `×2` beats two saying the same. */
const MIN_GROUP = 2;

export type TimelineRow =
  | { kind: 'event'; key: string; event: CodingEvent }
  | { kind: 'group'; key: string; eventType: string; toolName: string; events: CodingEvent[] };

/**
 * Consecutive calls of one tool, folded into a single countable row.
 *
 * A turn that reads twelve files is twelve rows of `Read` that push everything
 * else off the screen; what the reader wants from them is "it read twelve files"
 * and, on demand, which twelve. Only *adjacent* calls fold, so the fold never
 * reorders the stream or hides that the run went away and came back.
 */
export function groupEvents(events: CodingEvent[]): TimelineRow[] {
  const rows: TimelineRow[] = [];
  let run: CodingEvent[] = [];

  const flush = () => {
    if (run.length === 0) return;
    const [first] = run;
    if (run.length >= MIN_GROUP && first.tool_name !== null) {
      rows.push({
        kind: 'group',
        key: `g${first.id}`,
        eventType: first.event_type,
        toolName: first.tool_name,
        events: run,
      });
    } else {
      rows.push(...run.map((event) => ({ kind: 'event' as const, key: `e${event.id}`, event })));
    }
    run = [];
  };

  for (const event of events) {
    const groupable = event.tool_name !== null && !NEVER_GROUPED.has(event.tool_name);
    const continues =
      groupable &&
      run.length > 0 &&
      run[0].event_type === event.event_type &&
      run[0].tool_name === event.tool_name;
    if (!continues) flush();
    if (groupable) run.push(event);
    else rows.push({ kind: 'event', key: `e${event.id}`, event });
  }
  flush();
  return rows;
}

/**
 * What a prompt event actually says.
 *
 * Not every `UserPromptSubmit` is a person typing: a background task finishing
 * re-enters the session as one, carrying an XML envelope, and so does a system
 * reminder. Rendered raw they are a wall of markup where the reader expects a
 * sentence. The envelope writes its own one-liner — use it, and leave the rest
 * to the payload disclosure the row already has.
 */
const NOTIFICATION_PROMPT = '<task-notification>';
const REMINDER_PROMPT = '<system-reminder>';

export interface PromptText {
  text: string;
  /** True when the session was re-entered by something other than a person. */
  automated: boolean;
}

export function promptText(event: CodingEvent): PromptText | null {
  if (event.event_type !== 'UserPromptSubmit') return null;
  const raw: unknown = event.payload?.prompt;
  if (typeof raw !== 'string') return null;
  const prompt = raw.trim();
  if (prompt === '') return null;

  if (prompt.startsWith(NOTIFICATION_PROMPT)) {
    const summary = tagBody(prompt, 'summary');
    const status = tagBody(prompt, 'status') ?? 'finished';
    return { text: summary ?? `Background task ${status}`, automated: true };
  }
  if (prompt.startsWith(REMINDER_PROMPT)) {
    return { text: 'System reminder', automated: true };
  }
  return { text: prompt, automated: false };
}

export interface FirstRequest {
  /** The prompt event itself, so its images can be read off the payload. */
  event: CodingEvent;
  /** The whole prompt, as typed. */
  text: string;
}

/**
 * The request that started the run: the first prompt a *person* sent.
 *
 * The title is a summary of this, so the prompt itself is no longer on the
 * card — this is where a reader goes to check what the summary summarised.
 * Envelopes are skipped: a run resumed by a background task before its human
 * ever typed still has a human request further down the stream.
 */
export function firstRequest(events: CodingEvent[]): FirstRequest | null {
  for (const event of events) {
    const prompt = promptText(event);
    if (prompt && !prompt.automated) return { event, text: prompt.text };
  }
  return null;
}

function tagBody(text: string, tag: string): string | null {
  const match = new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`).exec(text);
  return match ? match[1].trim() || null : null;
}

export interface ToolSummary {
  /** What goes in the row. */
  text: string;
  /** The whole value, for the tooltip. */
  title: string;
}

export function toolSummary(event: CodingEvent): ToolSummary | null {
  const input = event.payload?.tool_input;
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null;
  const fields = input as Record<string, unknown>;

  for (const key of SUMMARY_KEYS) {
    const raw = fields[key];
    if (typeof raw !== 'string') continue;
    const value = raw.trim();
    if (value === '') continue;
    if (PATH_KEYS.has(key)) return { text: fileName(value), title: shortenPath(value) };
    const flat = value.replace(/\s+/g, ' ');
    return { text: truncate(flat), title: flat };
  }
  return null;
}

/** The last segment of a path — the part a person recognises the file by. */
function fileName(path: string): string {
  const trimmed = path.replace(/\/+$/, '');
  return trimmed.slice(trimmed.lastIndexOf('/') + 1) || trimmed;
}

function truncate(value: string): string {
  return value.length <= MAX_SUMMARY_CHARS ? value : `${value.slice(0, MAX_SUMMARY_CHARS)}…`;
}
