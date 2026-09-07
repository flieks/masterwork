import {
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Loader2,
  MessageCircleQuestion,
  MinusCircle,
  MoonStar,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

/**
 * Run and phase statuses are free strings in the contract, so both lookups have
 * a neutral fallback: a status the backend grows tomorrow still renders.
 */

export interface StatusMeta {
  label: string;
  icon: LucideIcon;
  /** Text + background classes for a chip. */
  chip: string;
  /** Background class for a bare dot. */
  dot: string;
  /** True for statuses that should read as a problem. */
  error: boolean;
  spin: boolean;
  /** Why this status means what it means, when that isn't obvious. */
  hint?: string;
}

const NEUTRAL: Omit<StatusMeta, 'label'> = {
  icon: CircleDashed,
  chip: 'bg-muted text-muted-foreground',
  dot: 'bg-muted-foreground/40',
  error: false,
  spin: false,
};

const OK = {
  icon: CheckCircle2,
  chip: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  dot: 'bg-emerald-500',
  error: false,
  spin: false,
};

const BAD = {
  icon: XCircle,
  chip: 'bg-red-500/15 text-red-700 dark:text-red-400',
  dot: 'bg-red-500',
  error: true,
  spin: false,
};

const BUSY = {
  icon: Loader2,
  chip: 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
  dot: 'bg-sky-500',
  error: false,
  spin: true,
};

/** A status whose own name does not read as a label carries one; the rest are
 * shown verbatim, so a status the backend grows tomorrow still renders. */
const RUN_STATUSES: Record<string, Omit<StatusMeta, 'label'> & { label?: string }> = {
  success: OK,
  failed: BAD,
  running: BUSY,
  // Interrupted is not a failure: the run was cut short, which says nothing
  // about the work — keep it neutral (same call the simulation screen makes).
  interrupted: { ...NEUTRAL, icon: CircleSlash },
  // The one status that is someone's to act on, so it is the one that gets a
  // colour of its own — amber reads as "held up", not as "broken".
  waiting_input: {
    label: 'waiting',
    icon: MessageCircleQuestion,
    chip: 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
    dot: 'bg-amber-500',
    error: false,
    spin: false,
    hint: 'Blocked on you. The run asked a question or hit a permission prompt mid-turn and is waiting for an answer — it will sit here until it gets one, or until the process dies.',
  },
  // Neither an error nor "still going": the run simply stopped reporting. Most
  // runs end this way, so a red chip here would paint the whole grid red.
  abandoned: {
    ...NEUTRAL,
    icon: MoonStar,
    hint: "Went quiet without reporting an outcome. Claude Code's SessionEnd hook dies with the process, so most runs never close themselves — after two minutes of silence the run is treated as finished rather than left running forever.",
  },
};

const PHASE_STATUSES: Record<string, Omit<StatusMeta, 'label'>> = {
  passed: OK,
  failed: BAD,
  running: BUSY,
  skipped: { ...NEUTRAL, icon: MinusCircle },
  // The stage-level twin of an abandoned run, and not a failure either: the
  // turn ended — the next one starting proves it — but nothing recorded when.
  abandoned: {
    ...NEUTRAL,
    icon: MoonStar,
    hint: 'Never reported its end. Closed when the next turn on this lane started, so its length is an upper bound, not a measurement.',
  },
};

export function runStatusMeta(status: string): StatusMeta {
  const meta = RUN_STATUSES[status] ?? NEUTRAL;
  return { ...meta, label: meta.label ?? status };
}

export function phaseStatusMeta(status: string): StatusMeta {
  return { label: status, ...(PHASE_STATUSES[status] ?? NEUTRAL) };
}
