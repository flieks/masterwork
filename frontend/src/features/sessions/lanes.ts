import type { AgentLane } from '~/api/generated';

/**
 * Lane identity: which rows a run's chart has, and what colour each one is.
 *
 * The factory sets `AgentLane.color` for its stages, but those hexes are picked
 * against a dark terminal. We keep the *hue* the producer chose and re-light it
 * for whichever theme the app is in, so a lane is recognisable and readable on
 * both surfaces. Lanes that report no colour get a stable hue from their name.
 */

/** Distinct, well-separated hues — no two adjacent entries read as the same colour. */
const FALLBACK_HUES = [212, 152, 275, 28, 330, 190, 96, 350];

const DEFAULT_SATURATION = 68;

export interface LaneTint {
  /** Lane label / glyph colour. */
  text: string;
  /** Translucent block fill — legible over both card and background surfaces. */
  fill: string;
  border: string;
}

export interface LanePhaseLike {
  agent: string | null;
}

export interface LaneGroup<P extends LanePhaseLike> {
  lane: AgentLane;
  phases: P[];
}

function hash(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Hue + saturation of a `#rgb` / `#rrggbb` colour, or null if unparseable. */
function hexToHueSat(color: string): { hue: number; saturation: number } | null {
  const hex = color.trim().replace(/^#/, '');
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((c) => c + c)
          .join('')
      : hex;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;

  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  if (delta === 0) return { hue: 0, saturation: 0 };

  let hue: number;
  if (max === r) hue = ((g - b) / delta) % 6;
  else if (max === g) hue = (b - r) / delta + 2;
  else hue = (r - g) / delta + 4;
  hue = Math.round(hue * 60);
  if (hue < 0) hue += 360;

  const lightness = (max + min) / 2;
  const saturation = Math.round((delta / (1 - Math.abs(2 * lightness - 1))) * 100);
  return { hue, saturation: Math.min(Math.max(saturation, 45), 85) };
}

/** The hue a lane draws in — the producer's, or a stable one from its name. */
export function laneHueSat(lane: { name: string; color?: string | null }): {
  hue: number;
  saturation: number;
} {
  const fromApi = lane.color ? hexToHueSat(lane.color) : null;
  if (fromApi && fromApi.saturation > 0) return fromApi;
  return {
    hue: FALLBACK_HUES[hash(lane.name) % FALLBACK_HUES.length],
    saturation: DEFAULT_SATURATION,
  };
}

/**
 * CSS colours for a lane. `light-dark()` picks the lightness per theme — the app
 * sets `color-scheme: light dark` on the root, and dark mode follows the OS.
 */
export function laneTint(lane: { name: string; color?: string | null }): LaneTint {
  const { hue, saturation } = laneHueSat(lane);
  const s = `${saturation}%`;
  return {
    text: `light-dark(hsl(${hue} ${s} 34%), hsl(${hue} ${s} 70%))`,
    fill: `hsl(${hue} ${s} 50% / 0.16)`,
    border: `hsl(${hue} ${s} 50% / 0.42)`,
  };
}

/** A lane for a session that reported none — every run gets at least one row. */
export function implicitLane(name = 'session'): AgentLane {
  return {
    name,
    model: null,
    color: null,
    context_tokens: null,
    context_window: null,
    cost_usd: null,
    tokens_in: null,
    tokens_out: null,
    turns: 0,
  };
}

/** Phases that name a lane nobody declared still need a row of their own. */
const UNASSIGNED = 'unassigned';

/**
 * One row per lane, phases attached to their owner. Declared lanes keep the
 * order the API gave them (first appearance); lanes only a phase knows about
 * are appended, so nothing in a run is silently dropped.
 */
export function buildLanes<P extends LanePhaseLike>(
  agents: AgentLane[],
  phases: P[],
): LaneGroup<P>[] {
  const groups = new Map<string, LaneGroup<P>>();
  for (const lane of agents) {
    groups.set(lane.name, { lane, phases: [] });
  }
  for (const phase of phases) {
    const key = phase.agent ?? UNASSIGNED;
    let group = groups.get(key);
    if (!group) {
      group = { lane: implicitLane(key), phases: [] };
      groups.set(key, group);
    }
    group.phases.push(phase);
  }
  return [...groups.values()];
}
