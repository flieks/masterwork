import type { ObservabilityIntegration } from '~/api/generated';

const EVENTS = [
  'SessionStart',
  'UserPromptSubmit',
  'PreToolUse',
  'PostToolUse',
  'SubagentStop',
  'Stop',
  'SessionEnd',
];

/** A Claude Code integration in whichever state a test needs. */
export function integration(
  overrides: Partial<ObservabilityIntegration> = {},
): ObservabilityIntegration {
  return {
    id: 'claude-code',
    label: 'Claude Code',
    state: 'connected',
    detail: 'Recording every Claude Code session to http://localhost:8008/api/v1/hooks/events.',
    ingest_url: 'http://localhost:8008/api/v1/hooks/events',
    events: EVENTS,
    config_path: '/home/dev/.claude/settings.json',
    script_path: '/home/dev/.masterwork/hooks/claude_code.py',
    backup_path: null,
    ...overrides,
  };
}

export const disconnected = () =>
  integration({
    state: 'disconnected',
    detail:
      "Claude Code isn't reporting its sessions yet. Connecting adds 7 hooks to " +
      '/home/dev/.claude/settings.json — nothing else on your machine changes.',
  });

export const outdated = () =>
  integration({
    state: 'outdated',
    detail:
      'The hooks point at a forwarder script that is no longer on disk — an upgrade or a ' +
      'cache clean removed it. Reconnecting puts it back.',
  });

export const unavailable = () =>
  integration({
    state: 'unavailable',
    detail:
      "Claude Code hasn't run on this machine yet — /home/dev/.claude doesn't exist. " +
      'Start it once, then connect.',
  });
