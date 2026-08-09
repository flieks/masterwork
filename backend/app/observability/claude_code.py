"""Claude Code observability: hook entries in `~/.claude/settings.json`.

The events below are the ones the Sessions screen is derived from — a session's
start and end, every prompt, every tool call, and the spawn of a subagent. The
rest of Claude Code's hook surface tells us nothing we don't already read.

The file belongs to the user, not to us: every write backs it up first, touches
only the hook entries whose command runs our forwarder, and leaves the file
untouched when there is nothing to change.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from app.core.exceptions import ObservabilityIOError, ObservabilityUnavailableError
from app.observability.base import (
    IntegrationState,
    IntegrationStatus,
    install_forwarder,
    resolve_interpreter,
)

EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
    "Stop",
    "SessionEnd",
]

# PreToolUse is subscribed for the subagent-spawn tool alone — it is the only
# event that knows when a subagent started, and matching every tool would double
# the event stream to learn nothing PostToolUse does not already say.
MATCHERS: dict[str, str | None] = {"PreToolUse": "Task|Agent"}

# How an entry of ours is recognised in a file we did not write in this process:
# anything running out of masterwork's home, plus the script name used before the
# forwarder moved there (clones that ran the old scripts/install-claude-hooks.py).
MARKERS = ("masterwork", "claude-hook-observe.py")


def _is_ours(hook: object) -> bool:
    return (
        isinstance(hook, dict)
        and isinstance(hook.get("command"), str)
        and any(marker in hook["command"] for marker in MARKERS)
    )


def _hook_entry(event: str, command: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": "command", "command": command, "timeout": 5}
    if event != "SessionEnd":
        # async everywhere except SessionEnd: the process may exit before a
        # background POST lands.
        entry["async"] = True
    return entry


def _groups(hooks: dict[str, Any], event: str) -> list[dict[str, Any]]:
    """The matcher groups registered for one event, skipping malformed entries."""
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return []
    return [group for group in groups if isinstance(group, dict)]


def _ours_in(hooks: dict[str, Any], event: str) -> list[tuple[str | None, dict[str, Any]]]:
    """Our hooks for one event, each paired with the matcher it sits under."""
    found: list[tuple[str | None, dict[str, Any]]] = []
    for group in _groups(hooks, event):
        entries = group.get("hooks")
        if not isinstance(entries, list):
            continue
        found.extend((group.get("matcher"), entry) for entry in entries if _is_ours(entry))
    return found


def _without_ours(hooks: dict[str, Any], event: str) -> list[dict[str, Any]]:
    """Every group for one event with our hooks removed — a group that held
    someone else's hook alongside ours survives, minus ours."""
    kept: list[dict[str, Any]] = []
    for group in _groups(hooks, event):
        entries = group.get("hooks")
        if not isinstance(entries, list):
            kept.append(group)  # not ours to interpret, so not ours to touch
            continue
        remaining = [entry for entry in entries if not _is_ours(entry)]
        if remaining:
            kept.append({**group, "hooks": remaining})
    return kept


class ClaudeCodeIntegration:
    """Wires Claude Code's hooks to the ingest endpoint."""

    id = "claude-code"
    label = "Claude Code"

    def __init__(
        self,
        *,
        settings_path: Path,
        hooks_dir: Path,
        forwarder: Path,
        ingest_url: str,
    ) -> None:
        self._settings_path = settings_path
        self._hooks_dir = hooks_dir
        self._forwarder = forwarder
        self._ingest_url = ingest_url

    # ── read ─────────────────────────────────────────────────────────────────

    def status(self) -> IntegrationStatus:
        blocker = self._blocker()
        if blocker:
            return self._status("unavailable", blocker)
        try:
            settings = self._read()
        except ObservabilityIOError as exc:
            return self._status("unavailable", exc.detail)
        return self._status(*self._assess(settings))

    def _assess(self, settings: dict[str, Any]) -> tuple[IntegrationState, str]:
        hooks = _hooks_of(settings)
        wired = {event: _ours_in(hooks, event) for event in EVENTS}
        if not any(wired.values()):
            return (
                "disconnected",
                f"Claude Code isn't reporting its sessions yet. Connecting adds {len(EVENTS)} "
                f"hooks to {self._settings_path} — nothing else on your machine changes.",
            )

        expected = self._expected()
        stale = [event for event in EVENTS if wired[event] != [expected[event]]]
        if stale:
            # Naming all seven reads as noise; naming a few is the useful case.
            which = "" if len(stale) == len(EVENTS) else f" ({', '.join(stale)})"
            return (
                "outdated",
                f"Wired to an older version of these hooks{which} — an upgrade moved them. "
                "Repairing points them back at this install; nothing else changes.",
            )
        if not self._installed_script().exists():
            return (
                "outdated",
                "The hooks point at a forwarder script that is no longer on disk — an "
                "upgrade or a cache clean removed it. Reconnecting puts it back.",
            )
        return "connected", f"Recording every Claude Code session to {self._ingest_url}."

    # ── write ────────────────────────────────────────────────────────────────

    def connect(self) -> IntegrationStatus:
        blocker = self._blocker()
        if blocker:
            raise ObservabilityUnavailableError(blocker)

        # A config we can't parse is the user's to fix, not ours to overwrite.
        try:
            before = self._read()
        except ObservabilityIOError as exc:
            raise ObservabilityUnavailableError(exc.detail) from exc
        install_forwarder(self._forwarder, self._hooks_dir, self._ingest_url)

        after = copy.deepcopy(before)
        hooks = _hooks_of(after)
        expected = self._expected()
        for event in EVENTS:
            matcher, entry = expected[event]
            group: dict[str, Any] = {"hooks": [entry]}
            if matcher:
                group = {"matcher": matcher, **group}
            hooks[event] = [*_without_ours(hooks, event), group]
        after["hooks"] = hooks

        self._write(before, after)
        return self.status()

    def disconnect(self) -> IntegrationStatus:
        try:
            before = self._read()
        except ObservabilityIOError as exc:
            raise ObservabilityUnavailableError(exc.detail) from exc

        after = copy.deepcopy(before)
        hooks = _hooks_of(after)
        for event in list(hooks):
            kept = _without_ours(hooks, event)
            # Drop the key when we were the only subscriber, so the file ends up
            # as it was before anyone connected.
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        if hooks:
            after["hooks"] = hooks
        else:
            after.pop("hooks", None)

        self._write(before, after)
        return self.status()

    # ── internals ────────────────────────────────────────────────────────────

    def _blocker(self) -> str | None:
        """Why connecting is impossible right now, or None."""
        if not self._settings_path.parent.exists():
            return (
                f"Claude Code hasn't run on this machine yet — {self._settings_path.parent} "
                "doesn't exist. Start it once, then connect."
            )
        interpreter = resolve_interpreter()
        if not interpreter or not os.access(interpreter, os.X_OK):
            return "No python3 on PATH to run the hook with."
        if not self._forwarder.exists():
            return f"This install is missing its forwarder script ({self._forwarder})."
        return None

    def _installed_script(self) -> Path:
        return self._hooks_dir / self._forwarder.name

    def _expected(self) -> dict[str, tuple[str | None, dict[str, Any]]]:
        """The matcher/hook pair each event should hold once connected."""
        # The agent runs this string through a shell, and both halves are paths
        # we don't choose: a home directory with a space in it would otherwise
        # split into two arguments. shlex leaves ordinary paths untouched, so
        # quoting costs nothing and no already-connected install churns.
        interpreter = shlex.quote(resolve_interpreter() or "python3")
        command = f"{interpreter} {shlex.quote(str(self._installed_script()))}"
        return {event: (MATCHERS.get(event), _hook_entry(event, command)) for event in EVENTS}

    def _read(self) -> dict[str, Any]:
        try:
            raw = self._settings_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ObservabilityIOError(
                f"could not read {self._settings_path}: {exc.strerror or exc}"
            ) from exc
        if not raw.strip():
            return {}
        try:
            settings = json.loads(raw)
        except ValueError as exc:
            raise ObservabilityIOError(
                f"{self._settings_path} is not valid JSON ({exc}). Fix it by hand first — "
                "connecting must never overwrite something you meant to keep."
            ) from exc
        if not isinstance(settings, dict):
            raise ObservabilityIOError(f"{self._settings_path} does not hold a JSON object.")
        if "hooks" in settings and not isinstance(settings["hooks"], dict):
            raise ObservabilityIOError(
                f'the "hooks" key in {self._settings_path} is not an object — leaving it alone.'
            )
        return settings

    def _write(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Back up and rewrite, unless nothing actually changed. Disconnecting an
        agent that was never connected must not conjure a settings file."""
        if before == after:
            return
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            if self._settings_path.exists():
                shutil.copy2(self._settings_path, self._backup_path())
            self._settings_path.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ObservabilityIOError(
                f"could not write {self._settings_path}: {exc.strerror or exc}"
            ) from exc

    def _backup_path(self) -> Path:
        return self._settings_path.with_name(self._settings_path.name + ".masterwork.bak")

    def _status(self, state: IntegrationState, detail: str) -> IntegrationStatus:
        backup = self._backup_path()
        return IntegrationStatus(
            id=self.id,
            label=self.label,
            state=state,
            detail=detail,
            ingest_url=self._ingest_url,
            events=EVENTS,
            config_path=str(self._settings_path),
            script_path=str(self._installed_script()),
            backup_path=str(backup) if backup.exists() else None,
        )


def _hooks_of(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks")
    return hooks if isinstance(hooks, dict) else {}
