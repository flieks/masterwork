"""Which skills and which subagents a run actually reached for.

Four signals, because Claude Code reports none of this directly and the obvious
one barely fires: across 2 237 recorded tool calls there were **two** explicit
`Skill` calls and **zero** `Task` calls. What is actually there is a `Read` of a
`SKILL.md` (how a skill really loads) and a `SubagentStop` naming a transcript
file (how a subagent really ends). So both are read, and the transcript's
sidecar is opened to learn the agent's type.

Codex has neither a Skill tool nor a Read: it loads a skill by shelling out to
print the file (`sed -n '1,240p' ~/.agents/skills/<name>/SKILL.md`), and names
its subagents on `SubagentStart`/`SubagentStop` directly. Both are the same two
signals in different clothes, and are read as such.

Reading that sidecar is the one filesystem touch outside a session's first
event. It is legitimate — this is a single-user local tool that already reads
`~/.claude` — but it is capped, and it can never fail an ingest: an unreadable
transcript degrades to the name `subagent`, it does not raise.

Like `derive`, nothing here touches the database, which is what lets the
backfill replay stored events through exactly the code path a live hook takes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db.models.coding import (
    ASSET_AGENT,
    ASSET_SKILL,
    SPAWN_TOOLS,
    UNKNOWN_AGENT,
    USE_SKILL_CALL,
    USE_SKILL_READ,
    USE_SPAWN_CALL,
    USE_SUBAGENT_STOP,
)

# `<root>/skills/<name>/SKILL.md` under any agent's skills folder — Claude Code's
# `~/.claude/skills` (and the project-local `.claude/skills`), Codex's
# `~/.codex/skills`, and the shared `~/.agents/skills` both reach through links.
# Group 1 is the whole path, group 2 the skill's directory name, which is also
# its asset id. The lookahead lets the same pattern read a path out of a shell
# command, where it ends at a space or a quote rather than at the line's end.
SKILL_ROOTS = ("claude", "codex", "agents")
SKILL_PATH = re.compile(
    r"((?:[^\s'\"]*/)?\.(?:"
    + "|".join(SKILL_ROOTS)
    + r")/skills/([^/\s'\"]+)/SKILL\.md)(?=$|[\s'\";|&)>])"
)

# Tool inputs that carry a path. Glob names its target in `pattern`, Read in
# `file_path`; the rest are cheap to check and cost nothing when absent.
PATH_KEYS = ("file_path", "pattern", "path", "notebook_path")

# Tools whose target path says which skill a run loaded. Edit/Write are left
# out on purpose: authoring a skill is not using one.
SKILL_PATH_TOOLS = frozenset({"Read", "Glob"})

# Codex's shell tools, under every name it has shipped them as. A command naming
# a SKILL.md is how a Codex run loads a skill. Claude Code's Bash is left out:
# its skills load through Read, and a Bash that names one is usually a grep.
SHELL_TOOLS = frozenset({"exec_command", "exec", "shell", "shell_command", "local_shell"})
# Where a shell tool keeps its command: `cmd` (exec_command), `command` (shell,
# as a string or an argv list), `input` (the `exec` custom tool's script).
COMMAND_KEYS = ("cmd", "command", "input")

# The agent-type sidecar is a handful of keys; anything larger is not one.
MAX_SIDECAR_BYTES = 64 * 1024

# What the caller handed the asset, per signal. A spawn's `prompt` is the whole
# brief for a subagent, so it is kept long enough to read and no longer.
INPUT_KEYS: dict[str, tuple[str, ...]] = {
    USE_SKILL_CALL: ("args",),
    USE_SPAWN_CALL: ("description", "prompt", "subagent_type", "model"),
}
MAX_INPUT_CHARS = 2_000


@dataclass(slots=True, frozen=True)
class AssetUse:
    """One use of one asset by one lane, before it is counted into a row."""

    kind: str
    name: str
    lane: str | None
    # USE_* — which signal named it, and so what `input` could hold.
    source: str
    # The call's arguments, already truncated; None when the signal carried none.
    input: dict[str, str] | None = None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sub(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    nested = (payload or {}).get(key)
    return nested if isinstance(nested, dict) else {}


def from_event(
    event_type: str,
    tool_name: str | None,
    payload: dict[str, Any] | None,
    *,
    lane: str | None,
) -> list[AssetUse]:
    """Every asset this one event says the run used. Usually none."""
    if event_type == "SubagentStop":
        return [AssetUse(ASSET_AGENT, subagent_name(payload), lane, USE_SUBAGENT_STOP)]
    if event_type == "SubagentStart":
        # Codex's spawn: the type and id are all it says, and all the log can hold.
        stated = {
            key: value
            for key in ("agent_type", "agent_id")
            if (value := _text((payload or {}).get(key)))
        }
        name = subagent_name(payload)
        return [AssetUse(ASSET_AGENT, name, lane, USE_SPAWN_CALL, stated or None)]
    # PreToolUse fires even when the call is denied, so only a completed call
    # counts — the same rule `tool_call_count` uses.
    if event_type != "PostToolUse" or not tool_name:
        return []

    tool_input = _sub(payload, "tool_input")
    if tool_name == "Skill":
        skill = _text(tool_input.get("skill"))
        if not skill:
            return []
        args = _inputs(tool_input, USE_SKILL_CALL)
        return [AssetUse(ASSET_SKILL, skill, lane, USE_SKILL_CALL, args)]
    if tool_name in SPAWN_TOOLS:
        subagent = _text(tool_input.get("subagent_type"))
        if not subagent:
            return []
        args = _inputs(tool_input, USE_SPAWN_CALL)
        return [AssetUse(ASSET_AGENT, subagent, lane, USE_SPAWN_CALL, args)]
    if tool_name in SKILL_PATH_TOOLS:
        skill = _skill_from_paths(tool_input)
        if not skill:
            return []
        # No arguments exist — a read is how a skill loads, not how it is called.
        # The path is kept so the log can still say where the use was seen.
        path = next((p for key in PATH_KEYS if (p := _text(tool_input.get(key)))), None)
        args = {"path": path} if path else None
        return [AssetUse(ASSET_SKILL, skill, lane, USE_SKILL_READ, args)]
    if tool_name in SHELL_TOOLS:
        command = command_text(tool_input)
        match = SKILL_PATH.search(command) if command else None
        if match is None:
            return []
        skill, path = match.group(2), match.group(1)
        return [AssetUse(ASSET_SKILL, skill, lane, USE_SKILL_READ, {"path": path})]
    return []


def command_text(tool_input: dict[str, Any]) -> str | None:
    """The shell command a tool call ran, whichever key its tool keeps it under
    and whether it was sent as one string or an argv list."""
    for key in COMMAND_KEYS:
        value = tool_input.get(key)
        if isinstance(value, list):
            value = " ".join(str(part) for part in value)
        if text := _text(value):
            return text
    return None


def _inputs(tool_input: dict[str, Any], source: str) -> dict[str, str] | None:
    """The argument keys this signal carries, stringified and truncated."""
    found: dict[str, str] = {}
    for key in INPUT_KEYS[source]:
        value = tool_input.get(key)
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if stripped := text.strip():
            found[key] = stripped[:MAX_INPUT_CHARS]
    return found or None


def _skill_from_paths(tool_input: dict[str, Any]) -> str | None:
    for key in PATH_KEYS:
        value = _text(tool_input.get(key))
        match = SKILL_PATH.search(value) if value else None
        if match is not None:
            return match.group(2)
    return None


def subagent_name(payload: dict[str, Any] | None) -> str:
    """The subagent's type, from the hook if it said, else from the transcript
    it points at, else the honest placeholder."""
    stated = _text((payload or {}).get("agent_type"))
    if stated:
        return stated
    transcript = (payload or {}).get("agent_transcript_path")
    return _agent_type_from_transcript(transcript) or UNKNOWN_AGENT


def _agent_type_from_transcript(raw_path: Any) -> str | None:
    """Read `<transcript>.meta.json`, which is where the agent's type is written.

    Best effort by contract: a missing, oversized, unreadable or unexpected file
    is simply no answer. The transcript itself is not parsed — it is megabytes
    of conversation and does not carry the type at the top level.
    """
    path = _text(raw_path)
    if path is None or not path.endswith(".jsonl"):
        return None
    try:
        sidecar = Path(path).with_suffix(".meta.json")
        if not sidecar.is_file() or sidecar.stat().st_size > MAX_SIDECAR_BYTES:
            return None
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return None
    return _text(data.get("agentType")) if isinstance(data, dict) else None
