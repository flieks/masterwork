"""Claude Code observability: hook entries in `~/.claude/settings.json`.

The events below are the ones the Sessions screen is derived from — a session's
start and end, every prompt, every tool call, and the spawn of a subagent. The
rest of Claude Code's hook surface tells us nothing we don't already read.
"""

from __future__ import annotations

from app.observability.json_hooks import JsonHooksIntegration

EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "SubagentStop",
    "Stop",
    "SessionEnd",
]

# PreToolUse is subscribed for the subagent-spawn tool alone — it is the only
# event that knows when a subagent started, and matching every tool would double
# the event stream to learn nothing PostToolUse does not already say.
MATCHERS: dict[str, str | None] = {"PreToolUse": "Task|Agent"}

# Anything running out of masterwork's home, plus the script name used before the
# forwarder moved there (clones that ran the old scripts/install-claude-hooks.py).
MARKERS = ("masterwork", "claude-hook-observe.py")


class ClaudeCodeIntegration(JsonHooksIntegration):
    """Wires Claude Code's hooks to the ingest endpoint."""

    id = "claude-code"
    label = "Claude Code"
    events = EVENTS
    matchers = MATCHERS
    markers = MARKERS
