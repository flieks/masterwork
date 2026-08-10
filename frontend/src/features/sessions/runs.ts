import type { CodingSession } from '~/api/generated';
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

/** True while the session is open and still producing events. */
export function isSessionLive(session: CodingSession, now = Date.now()): boolean {
  if (session.ended_at !== null) return false;
  const last = new Date(session.last_event_at).getTime();
  if (Number.isNaN(last)) return false;
  return now - last < LIVE_WINDOW_MS;
}

/** True when a `claude -p` one-shot started the run — a script, hook or scheduler. */
export function isAutomatedSession(session: CodingSession): boolean {
  return session.launch_mode === 'automated';
}

/** Repo name, falling back to the last path segment of the cwd. */
export function sessionLabel(session: CodingSession): string {
  if (session.git_repo) return session.git_repo;
  const segments = session.cwd.split('/').filter(Boolean);
  return segments.length > 0 ? segments[segments.length - 1] : session.id;
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
 * The `interrupted` filter always comes back empty today, and "no runs matched"
 * would read as a fact about the runs. Masterwork cannot tell a killed process
 * from a lost hook, so it never derives this status — only the tool that ran
 * the session can report one, and nothing does yet. The option stays because
 * that can change; the empty state says which of the two it is.
 */
export const INTERRUPTED_NEVER_DERIVED =
  'Masterwork never derives this status — only the tool that ran the session can report it, and nothing does yet.';
