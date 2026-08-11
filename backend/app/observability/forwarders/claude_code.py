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

# The pipeline runner exports these into the environment of each `claude -p`
# stage child. They are the whole reason a stage can be attached to its run
# without reading a command line: the runner *states* what it launched, so
# nothing downstream has to infer it from a cwd, an argv or a time window.
# Absent for every ordinary session, which is what keeps the signal meaningful.
FACTORY_RUN_ID_ENV = "MASTERWORK_FACTORY_RUN_ID"
FACTORY_STAGE_ENV = "MASTERWORK_FACTORY_STAGE"

# Column-bound on the ingest side; truncate here so a runaway env var still posts.
MAX_RUN_ID = 200
MAX_STAGE = 100


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


def factory_stage() -> dict[str, str]:
    """What the pipeline runner says this session is, if it said anything.

    Only the run id is required: a stage whose name went missing still belongs
    under its run, and the missing half costs a title, not the link.
    """
    run_id = (os.environ.get(FACTORY_RUN_ID_ENV) or "").strip()
    if not run_id:
        return {}
    stated = {"factory_run_id": run_id[:MAX_RUN_ID]}
    stage = (os.environ.get(FACTORY_STAGE_ENV) or "").strip()
    if stage:
        stated["factory_stage"] = stage[:MAX_STAGE]
    return stated


# USD per million tokens, (input, output), keyed by the family name inside the
# model id — matching on the family rather than the exact id means a model
# released after this script was written is still priced, at its tier's rate.
# Cache is billed off the input rate everywhere: 0.1x to read, 1.25x to write a
# 5-minute entry, 2x for a 1-hour one.
PRICES: dict[str, tuple[float, float]] = {
    "fable": (10.0, 50.0),
    "mythos": (10.0, 50.0),
    "opus": (5.0, 25.0),
    "sonnet": (3.0, 15.0),
    "haiku": (1.0, 5.0),
}
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATES = {"ephemeral_5m_input_tokens": 1.25, "ephemeral_1h_input_tokens": 2.0}
# Fast mode runs the same model at premium pricing — exactly double on the
# models that offer it.
FAST_MULTIPLIER = 2.0


def price_of(model: str, usage: dict[str, Any]) -> tuple[float, float] | None:
    """The (input, output) rate one message billed at, or None if unrecognised —
    an unknown model still contributes its tokens, just not a price."""
    for family, rate in PRICES.items():
        if family in model:
            if usage.get("speed") == "fast":
                return rate[0] * FAST_MULTIPLIER, rate[1] * FAST_MULTIPLIER
            return rate
    return None


def transcript_usage(path: str) -> dict[str, Any]:
    """Roll a session's tokens and cost out of its transcript file.

    Claude Code records no cost anywhere — only per-message `usage` — so the
    number on the card has to be computed, and this is the only place that sees
    both the usage and the model that billed it. Subagent turns live in the same
    file and count too.

    One API response can span several transcript lines (text and a tool call are
    written separately) and each carries the *same* cumulative usage, so the
    dedupe by message id is what keeps the total from doubling.
    """
    cost = 0.0
    tokens_in = tokens_out = cache_read = cache_write = 0
    seen: set[str] = set()
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a half-written last line while the session runs
                message = record.get("message")
                if record.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                message_id = message.get("id")
                if not isinstance(usage, dict) or not isinstance(message_id, str):
                    continue
                if message_id in seen:
                    continue
                seen.add(message_id)

                plain_in = int(usage.get("input_tokens") or 0)
                out = int(usage.get("output_tokens") or 0)
                read = int(usage.get("cache_read_input_tokens") or 0)
                written = usage.get("cache_creation") or {}
                write_total = int(usage.get("cache_creation_input_tokens") or 0)

                tokens_in += plain_in + read + write_total
                tokens_out += out
                cache_read += read
                cache_write += write_total

                rate = price_of(str(message.get("model") or ""), usage)
                if rate is None:
                    continue
                billed = plain_in + read * CACHE_READ_RATE
                by_ttl = sum(int(written.get(key) or 0) for key in CACHE_WRITE_RATES)
                if by_ttl:
                    for key, multiplier in CACHE_WRITE_RATES.items():
                        billed += int(written.get(key) or 0) * multiplier
                else:
                    # No per-TTL breakdown (older transcripts): assume the 5m default.
                    billed += write_total * CACHE_WRITE_RATES["ephemeral_5m_input_tokens"]
                cost += (billed * rate[0] + out * rate[1]) / 1_000_000
    except OSError:
        return {}
    if not seen:
        return {}
    return {
        "cost_usd": round(cost, 6),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


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
        # Provenance, strongest first: what the launcher declared itself to be,
        # then the ancestry it can only be guessed from.
        payload.update(factory_stage())
        payload["launched_by"] = ancestry()
        for key in ("transcript_path", "permission_mode"):
            if raw.get(key):
                payload[key] = raw[key]
    elif event == "SessionEnd":
        body["ended"] = True
        if raw.get("reason"):
            payload["reason"] = raw["reason"]

    # End of a turn is the first moment the transcript holds a complete answer,
    # and SessionEnd is the last chance to read it. The totals are absolute, so
    # a re-read simply restates them.
    if event in ("Stop", "SessionEnd") and raw.get("transcript_path"):
        stats = transcript_usage(raw["transcript_path"])
        if stats:
            body["stats"] = stats

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
