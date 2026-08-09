import { Badge } from '~/components/ui/badge';
import { cn } from '~/lib/utils';

/**
 * A tool's name, coloured by what the tool *does*.
 *
 * Scanning a run means asking "where did it stop reading and start writing?",
 * and a wall of identically-styled badges answers that only by being read word
 * by word. Colour is grouped by act, not by tool: every way of looking shares
 * one colour, every way of changing a file shares another. So an unfamiliar
 * tool still lands in a familiar band, and a burst of edits is visible from
 * across the page.
 *
 * A free string with a neutral fallback, like `EventTypeChip` — a tool that
 * ships tomorrow renders in grey rather than crashing.
 */
const LOOKING = 'bg-sky-500/15 text-sky-700 dark:text-sky-300';
const CHANGING = 'bg-amber-500/15 text-amber-700 dark:text-amber-300';
const RUNNING = 'bg-violet-500/15 text-violet-700 dark:text-violet-300';
const DELEGATING = 'bg-orange-500/15 text-orange-700 dark:text-orange-300';
const PLANNING = 'bg-teal-500/15 text-teal-700 dark:text-teal-300';
/** Skills get the loudest colour: which ones a run reached for is the point. */
const SKILL = 'bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300';

const TOOL_STYLES: Record<string, string> = {
  Read: LOOKING,
  Glob: LOOKING,
  Grep: LOOKING,
  ToolSearch: LOOKING,
  WebFetch: LOOKING,
  WebSearch: LOOKING,
  NotebookRead: LOOKING,

  Edit: CHANGING,
  Write: CHANGING,
  NotebookEdit: CHANGING,
  MultiEdit: CHANGING,

  Bash: RUNNING,
  BashOutput: RUNNING,
  KillShell: RUNNING,

  Task: DELEGATING,
  Agent: DELEGATING,
  Workflow: DELEGATING,
  SendMessage: DELEGATING,

  TaskCreate: PLANNING,
  TaskUpdate: PLANNING,
  TaskList: PLANNING,
  TaskGet: PLANNING,
  ExitPlanMode: PLANNING,
  EnterPlanMode: PLANNING,

  Skill: SKILL,
};

/** Anything a connected server provides — one band, whichever server it is. */
const MCP_PREFIX = 'mcp__';
const MCP = 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300';

function toolStyle(toolName: string): string | undefined {
  return TOOL_STYLES[toolName] ?? (toolName.startsWith(MCP_PREFIX) ? MCP : undefined);
}

export function ToolChip({ toolName, className }: { toolName: string; className?: string }) {
  const style = toolStyle(toolName);
  return (
    <Badge
      variant={style ? 'default' : 'muted'}
      className={cn('border-transparent font-mono text-[11px]', style, className)}
      title={toolName}
    >
      {toolName}
    </Badge>
  );
}
