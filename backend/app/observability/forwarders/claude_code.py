#!/usr/bin/env python3
"""Forward Claude Code hook events to masterwork's ingest endpoint. Fail-silent.

Runs as a standalone script under any stdlib python3 — never imported by the
app. `Integration.connect()` copies it to `~/.masterwork/hooks/` so the path
recorded in `settings.json` survives an npx cache prune, and drops a
`config.json` beside it naming the ingest URL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_INGEST_URL = "http://localhost:8008/api/v1/hooks/events"


def ingest_url() -> str:
    """Env first (a launcher can override per run), then the sidecar written at
    connect time, then the default port."""
    from_env = os.environ.get("MASTERWORK_INGEST_URL")
    if from_env:
        return from_env
    try:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
        url = config.get("ingest_url")
        if isinstance(url, str) and url:
            return url
    except (OSError, ValueError):
        pass
    return DEFAULT_INGEST_URL


def redact(args: str) -> str:
    """`claude -p '<prompt>'` carries conversation text in argv. Keep the flag — it
    marks a headless one-shot — and drop what follows it."""
    for flag in (" -p ", " --print "):
        head, found, _ = args.partition(flag)
        if found:
            return f"{head}{flag}…"
    return args


def ancestry(limit: int = 6) -> list[str]:
    """Walk the ppid chain so a session records what launched it, not just that it ran."""
    chain: list[str] = []
    pid = os.getppid()
    for _ in range(limit):
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,args=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=1,
            ).stdout.strip()
            ppid, _, args = out.partition(" ")
            chain.append(f"{pid} {redact(args.strip())[:600]}")
            pid = int(ppid)
        except Exception:
            break
        if pid <= 1:
            break
    return chain


def compact(value: Any, limit: int) -> Any:
    """Keep JSON structure when small; collapse to a truncated string when huge."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        return {"_unserializable": str(type(value))}
    if len(text) <= limit:
        return value
    return {"_truncated": text[:limit]}


def build_body(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Claude Code hook firing onto the ingest contract, or None to skip."""
    session_id = raw.get("session_id")
    if not session_id:
        return None
    event = raw.get("hook_event_name") or "Unknown"

    body: dict[str, Any] = {"session_id": session_id, "event_type": event}
    if raw.get("cwd"):
        body["cwd"] = raw["cwd"]
    if raw.get("tool_name"):
        body["tool_name"] = raw["tool_name"]

    payload: dict[str, Any] = {}
    if event == "UserPromptSubmit":
        prompt = raw.get("prompt", "")
        payload["prompt"] = prompt if len(prompt) <= 4000 else prompt[:4000] + "…"
    elif event == "PreToolUse":
        # Only fires for Task/Agent (see the installer's matcher): the spawn call
        # is the one place a subagent's start time is knowable.
        payload["tool_input"] = compact(raw.get("tool_input", {}), 4000)
    elif event == "PostToolUse":
        payload["tool_input"] = compact(raw.get("tool_input", {}), 4000)
        payload["tool_response"] = compact(raw.get("tool_response", {}), 2000)
    elif event == "SubagentStop":
        for key in ("agent_type", "agent_transcript_path"):
            if raw.get(key):
                payload[key] = raw[key]
    elif event == "SessionStart":
        payload["source"] = raw.get("source", "")
        # Provenance: which process spawned this run, and whether it is headless.
        payload["launched_by"] = ancestry()
        for key in ("transcript_path", "permission_mode"):
            if raw.get(key):
                payload[key] = raw[key]
    elif event == "SessionEnd":
        body["ended"] = True
        if raw.get("reason"):
            payload["reason"] = raw["reason"]
    if payload:
        body["payload"] = payload
    return body


def post(url: str, body: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=2)


def selftest() -> int:
    """Prove the wiring end to end: is this interpreter fine, and is the backend
    reachable at the URL the hook will post to? Used by `connect()`."""
    url = ingest_url()
    parts = urlsplit(url)
    health = f"{parts.scheme}://{parts.netloc}/health"
    try:
        with urllib.request.urlopen(health, timeout=2) as response:
            ok = response.status == 200
    except Exception as exc:
        print(f"unreachable {health}: {exc}", file=sys.stderr)
        return 1
    print(f"ok {url}" if ok else f"unhealthy {health}", file=sys.stderr)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    try:
        body = build_body(json.load(sys.stdin))
        if body is not None:
            post(ingest_url(), body)
    except Exception:
        # Observability must never break a coding session.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
