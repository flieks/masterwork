import type { CodingEvent, CodingSession } from '~/api/generated';
import { LIVE_WINDOW_MS } from '~/lib/timeline';

/**
 * How to read a run: its identity, its title and whether it is still going.
 *
 * A leaf module — no atoms, no API client — so the vocabulary is available to
 * anything that only needs to describe a run, including specs that run outside
 * a browser (where `import.meta.env`, which the client reads, does not exist).
 */

export { LIVE_WINDOW_MS };

export function sessionDetailPath(sessionId: string): string {
  return `/sessions/${encodeURIComponent(sessionId)}`;
}

/**
 * How long a run may stay silent and still count as live. Mirrors the backend's
 * two idle windows, and has to: the dot and the status chip on the same card
 * are computed on opposite sides of the wire, so a shorter window here says
 * "abandoned" over a run the API still calls running. A pipeline stage is never
 * quiet while it works; a chat is quiet for exactly as long as its human reads.
 */
export const FACTORY_IDLE_MS = 2 * 60 * 1000;
export const CHAT_IDLE_MS = 30 * 60 * 1000;

/** True while the session is open and still producing events. */
export function isSessionLive(session: CodingSession, now = Date.now()): boolean {
  if (session.ended_at !== null) return false;
  const last = new Date(session.last_event_at).getTime();
  if (Number.isNaN(last)) return false;
  return now - last < (runWorkflow(session) === 'factory' ? FACTORY_IDLE_MS : CHAT_IDLE_MS);
}

/** True when a `claude -p` one-shot started the run — a script, hook or scheduler. */
export function isAutomatedSession(session: CodingSession): boolean {
  return session.launch_mode === 'automated';
}

/**
 * Folder names that name a part of a product rather than the product — a run
 * in `Translation Tool/APP` is not a run in "APP".
 */
const GENERIC_DIRS = new Set([
  'android',
  'api',
  'app',
  'apps',
  'backend',
  'client',
  'frontend',
  'ios',
  'main',
  'mobile',
  'packages',
  'repo',
  'server',
  'src',
  'web',
]);

/**
 * Which project a run belongs to: the repo name, or the last path segment of
 * the cwd — qualified with the folder above it when that name alone says
 * nothing ("MoveMatch/mobile", not "mobile").
 */
export function sessionLabel(session: CodingSession): string {
  const segments = session.cwd.split('/').filter(Boolean);
  const leaf = session.git_repo || segments[segments.length - 1];
  if (!leaf) return session.id;
  if (!GENERIC_DIRS.has(leaf.toLowerCase())) return leaf;
  const parent = segments[segments.length - 2];
  return parent ? `${parent}/${leaf}` : leaf;
}

/**
 * One colour per project, hashed from its name. Colour answers "which app is
 * this?" before the word is read, and a hash keeps the answer stable — a
 * palette assigned in list order would repaint every card as projects come
 * and go.
 */
const PROJECT_TINTS = [
  'border-sky-500/25 bg-sky-500/15 text-sky-700 dark:text-sky-300',
  'border-violet-500/25 bg-violet-500/15 text-violet-700 dark:text-violet-300',
  'border-emerald-500/25 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  'border-amber-500/25 bg-amber-500/15 text-amber-700 dark:text-amber-300',
  'border-rose-500/25 bg-rose-500/15 text-rose-700 dark:text-rose-300',
  'border-cyan-500/25 bg-cyan-500/15 text-cyan-700 dark:text-cyan-300',
  'border-orange-500/25 bg-orange-500/15 text-orange-700 dark:text-orange-300',
  'border-fuchsia-500/25 bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300',
  'border-lime-500/25 bg-lime-500/15 text-lime-700 dark:text-lime-300',
  'border-indigo-500/25 bg-indigo-500/15 text-indigo-700 dark:text-indigo-300',
  'border-teal-500/25 bg-teal-500/15 text-teal-700 dark:text-teal-300',
  'border-pink-500/25 bg-pink-500/15 text-pink-700 dark:text-pink-300',
];

/**
 * The tint classes for a project name. Same name, same colour, always — and
 * hashed on the part before the slash, so a product's halves ("MoveMatch/api",
 * "MoveMatch/mobile") read as one colour.
 */
export function projectTint(project: string): string {
  const key = project.split('/')[0];
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) | 0;
  return PROJECT_TINTS[Math.abs(hash % PROJECT_TINTS.length)];
}

/**
 * The run's id as telemetry: factory runs already have a short readable id,
 * a chat session's uuid gets cut to its first block.
 */
export function runIdLabel(session: CodingSession): string {
  const id = session.id;
  if (id.length <= 20) return id;
  const firstBlock = id.split('-')[0];
  return firstBlock.length >= 8 ? firstBlock : id.slice(0, 8);
}

/** "factory" / "chat" — nothing ever writes "chat", so an absent workflow is one. */
export function runWorkflow(session: CodingSession): string {
  return session.workflow ?? 'chat';
}

/** What a `title_source` is worth saying out loud next to the title. */
const TITLE_HINTS: Record<string, string> = {
  factory: 'pipeline request',
  provenance: 'pipeline stage',
};

export interface RunTitle {
  text: string;
  source: string | null;
  /**
   * True when the "title" is only the folder the run happened in — the backend
   * derives it for runs that never said what they were doing, so it should read
   * as the absence of a title rather than as one.
   */
  weak: boolean;
  /** Where the title came from, when the provenance is worth showing. */
  hint: string | null;
}

/** The run's request, and how much to trust it. */
export function runTitleMeta(session: CodingSession): RunTitle {
  const text = session.title?.trim();
  const source = session.title_source;
  if (!text) return { text: sessionLabel(session), source: 'cwd', weak: true, hint: null };
  return {
    text,
    source,
    weak: source === 'cwd',
    hint: source ? (TITLE_HINTS[source] ?? null) : null,
  };
}

/** How far back the asset rollup looks. */
export type AssetWindow = '24h' | '7d' | 'all';

const WINDOW_HOURS: Record<Exclude<AssetWindow, 'all'>, number> = { '24h': 24, '7d': 24 * 7 };

/** The `since` bound for a window, or undefined for all time. */
export function windowSince(window: AssetWindow, now = Date.now()): string | undefined {
  if (window === 'all') return undefined;
  return new Date(now - WINDOW_HOURS[window] * 3_600_000).toISOString();
}

/** "4 stage runs" — a pipeline's stages, counted. Plural that reads right at one. */
export function stageRunsLabel(count: number): string {
  return `${count} stage ${count === 1 ? 'run' : 'runs'}`;
}

/**
 * The factory-or-chat router announces its verdict by running
 * `echo "masterwork:route=chat -- reason"` — echo exists everywhere, and the
 * hook forwarder ships the command here with the session id attached, which the
 * agent itself never knows. Latest marker wins: one session can route several
 * tasks.
 */
const ROUTE_MARKER = /masterwork:route=(chat|factory)(?:\s*--\s*([^"'\n]*))?/;

export interface RouteDecision {
  verdict: 'chat' | 'factory';
  reason: string | null;
}

/** The router's latest verdict in this session, or null if it never spoke. */
export function routeDecision(events: CodingEvent[]): RouteDecision | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const input = events[i].payload?.['tool_input'] as { command?: unknown } | undefined;
    const command = input?.command;
    if (typeof command !== 'string') continue;
    const match = ROUTE_MARKER.exec(command);
    if (!match) continue;
    const reason = match[2]?.trim();
    return { verdict: match[1] as RouteDecision['verdict'], reason: reason || null };
  }
  return null;
}

/**
 * The `interrupted` filter always comes back empty today, and "no runs matched"
 * would read as a fact about the runs. Masterwork cannot tell a killed process
 * from a lost hook, so it never derives this status — only the tool that ran
 * the session can report one, and nothing does yet. The option stays because
 * that can change; the empty state says which of the two it is.
 */
export const INTERRUPTED_NEVER_DERIVED =
  'Masterwork never derives this status — only the tool that ran the session can report it, and nothing does yet.';
