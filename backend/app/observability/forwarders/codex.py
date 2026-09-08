#!/usr/bin/env python3
"""Forward Codex hook events to masterwork's ingest endpoint. Fail-silent.

Runs as a standalone script under any stdlib python3 — never imported by the
app, and never importing the Claude Code forwarder beside it: the two are
copied onto disk separately, so what they share is copied, not imported.
`Integration.connect()` copies it to `~/.masterwork/hooks/` and drops a
`config.json` beside it naming the ingest URL.

Codex hands every event `session_id`, `cwd`, `hook_event_name`, `model`,
`transcript_path` and `permission_mode`, plus `turn_id` on turn-scoped ones.
The transcript is a rollout file, one JSON object per line, whose `token_count`
lines carry the thread's running token totals.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_INGEST_URL = "http://localhost:8008/api/v1/hooks/events"

# What the ingest files the session under. Its absence means Claude Code.
AGENT = "codex"

PROMPT_CHARS = 4000
# Codex hands the turn's final answer to Stop; enough of it to read, no more.
ANSWER_CHARS = 2000


def setting(env_var: str, key: str) -> str | None:
    """Env first (a launcher can override per run), then the sidecar `connect()`
    writes beside this script. None when neither says anything."""
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    try:
        config = json.loads(Path(__file__).with_name("config.json").read_text(encoding="utf-8"))
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    except (OSError, ValueError):
        pass
    return None


def ingest_url() -> str:
    return setting("MASTERWORK_INGEST_URL", "ingest_url") or DEFAULT_INGEST_URL


# A headless `codex exec "<prompt>"` carries the prompt in argv, as `claude -p`
# does; either may sit in the ancestry of a Codex session.
_HEADLESS_CODEX = re.compile(r"^(.*?(?:^|[/\s])codex\b.*?\sexec)(?:\s|$)")


def redact(args: str) -> str:
    """Keep the flag that marks a headless one-shot and drop the prompt after it."""
    match = _HEADLESS_CODEX.search(args)
    if match:
        return f"{match.group(1)} …"
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


# USD per million tokens, (input, output), by exact model name — a dated
# snapshot (`gpt-5-2025-08-07`) bills as its base name. Cached input is a tenth
# of the input rate. Nothing else is priced: a model this list has not heard of
# reports its tokens and no cost, rather than a number guessed off a neighbour.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5": (1.25, 10.0),
    "gpt-5.1": (1.25, 10.0),
    "gpt-5-codex": (1.25, 10.0),
    "gpt-5.1-codex": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
}
CACHE_READ_RATE = 0.1
_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def price_of(model: str) -> tuple[float, float] | None:
    return PRICES.get(_DATED.sub("", model.strip().lower()))


def _usage_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """The thread's running totals, if this rollout line restates them.

    Two line shapes say so: `event_msg/token_count` (every version) and the
    newer `token_usage_record`. Both are cumulative over the thread, so the last
    one seen is the session's total and nothing needs summing.
    """
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "token_usage_record":
        usage = payload.get("thread_token_usage")
        return usage if isinstance(usage, dict) else None
    if record.get("type") == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        usage = info.get("total_token_usage") if isinstance(info, dict) else None
        return usage if isinstance(usage, dict) else None
    return None


def _model_of(record: dict[str, Any]) -> str | None:
    """The model a rollout line names, for a hook that did not say."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "event_msg" and payload.get("type") == "thread_settings_applied":
        thread = payload.get("thread_settings")
        model = thread.get("model") if isinstance(thread, dict) else None
        return model if isinstance(model, str) else None
    if record.get("type") == "world_state":
        state = payload.get("state")
        mode = state.get("collaboration_mode") if isinstance(state, dict) else None
        model = mode.get("model") if isinstance(mode, dict) else None
        return model if isinstance(model, str) else None
    return None


def _lines(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a half-written last line while the session runs
            if isinstance(record, dict):
                records.append(record)
    return records


def transcript_usage(path: str, model: str | None = None) -> dict[str, Any]:
    """Roll a session's tokens — and its cost, when the model is priced — out of
    its rollout file. `model` is what the hook said; the transcript's own
    statement is the fallback."""
    totals: dict[str, Any] | None = None
    seen_model: str | None = None
    try:
        for record in _lines(path):
            totals = _usage_of(record) or totals
            seen_model = _model_of(record) or seen_model
    except OSError:
        return {}
    if totals is None:
        return {}

    tokens_in = int(totals.get("input_tokens") or 0)
    cached = int(totals.get("cached_input_tokens") or 0)
    tokens_out = int(totals.get("output_tokens") or 0)
    stats: dict[str, Any] = {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cache_read_tokens": cached,
    }
    rate = price_of(model or seen_model or "")
    if rate is not None:
        # `input_tokens` already includes the cached share, billed at its own rate.
        billed_in = (tokens_in - cached) + cached * CACHE_READ_RATE
        stats["cost_usd"] = round((billed_in * rate[0] + tokens_out * rate[1]) / 1_000_000, 6)
    return stats


# Bounded like everything else the hook posts: the tail of a long session is
# the interesting end of the context curve, so a cap keeps the *last* samples.
MAX_CONTEXT_SAMPLES = 2000
MAX_SAMPLE_TOOLS = 20
_CALL_TYPES = {"custom_tool_call", "function_call", "local_shell_call"}


def context_samples(path: str) -> list[dict[str, Any]]:
    """One sample per model response, from the `token_count` line Codex writes
    after each: `last_token_usage.input_tokens` is what the context held when
    that response was generated. Tool calls made since the previous sample ride
    along, so the growth can be attributed. Subagents run in their own rollout
    files, so nothing here is a sidechain."""
    samples: list[dict[str, Any]] = []
    pending: list[str] = []
    try:
        records = _lines(path)
    except OSError:
        return []
    for index, record in enumerate(records):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "response_item" and payload.get("type") in _CALL_TYPES:
            name = payload.get("name")
            if isinstance(name, str) and name:
                pending.append(name)
            continue
        if record.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        last = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(last, dict):
            continue
        ordinal = record.get("ordinal")
        sample: dict[str, Any] = {
            "seq": len(samples) + 1,
            "message_id": f"turn-{ordinal}" if isinstance(ordinal, int) else f"line-{index}",
            "total_tokens": int(last.get("input_tokens") or 0),
            "output_tokens": int(last.get("output_tokens") or 0),
            "is_sidechain": False,
            "tools": pending[:MAX_SAMPLE_TOOLS],
        }
        at = record.get("timestamp")
        if isinstance(at, str):
            sample["at"] = at
        pending = []
        samples.append(sample)
    return samples[-MAX_CONTEXT_SAMPLES:]


def compact(value: Any, limit: int) -> Any:
    """Keep JSON structure when small; collapse to a truncated string when huge."""
    try:
        text = json.dumps(value, default=str)
    except Exception:
        return {"_unserializable": str(type(value))}
    if len(text) <= limit:
        return value
    return {"_truncated": text[:limit]}


def tool_input_of(raw: dict[str, Any]) -> Any:
    """Codex's custom tools (`exec`) take a bare string; the ingest reads tool
    input as an object, so a string is filed under the key Codex itself uses."""
    tool_input = raw.get("tool_input", {})
    return {"input": tool_input} if isinstance(tool_input, str) else tool_input


def build_body(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Codex hook firing onto the ingest contract, or None to skip."""
    session_id = raw.get("session_id")
    if not session_id:
        return None
    event = raw.get("hook_event_name") or "Unknown"

    body: dict[str, Any] = {"session_id": session_id, "event_type": event, "source": AGENT}
    for key in ("cwd", "model", "tool_name"):
        if raw.get(key):
            body[key] = raw[key]

    payload: dict[str, Any] = {}
    if raw.get("turn_id"):
        payload["turn_id"] = raw["turn_id"]

    if event == "UserPromptSubmit":
        prompt = raw.get("prompt", "")
        payload["prompt"] = prompt if len(prompt) <= PROMPT_CHARS else prompt[:PROMPT_CHARS] + "…"
    elif event in ("PreToolUse", "PostToolUse", "PermissionRequest"):
        payload["tool_input"] = compact(tool_input_of(raw), 4000)
        if event == "PostToolUse":
            payload["tool_response"] = compact(raw.get("tool_response", {}), 2000)
        elif event == "PermissionRequest":
            # The one event that fires *because* nothing is happening: Codex is
            # waiting for the person to approve a tool call.
            payload["message"] = (
                f"Codex needs your permission to run {raw.get('tool_name') or 'a tool'}"
            )
    elif event in ("SubagentStart", "SubagentStop"):
        for key in ("agent_type", "agent_id"):
            if raw.get(key):
                payload[key] = raw[key]
    elif event == "SessionStart":
        payload["source"] = raw.get("source", "")
        payload["launched_by"] = ancestry()
        for key in ("transcript_path", "permission_mode"):
            if raw.get(key):
                payload[key] = raw[key]
    elif event == "SessionEnd":
        body["ended"] = True
        if raw.get("reason"):
            payload["reason"] = raw["reason"]
    elif event in ("Stop", "Interrupt"):
        answer = raw.get("last_assistant_message")
        if isinstance(answer, str) and answer:
            payload["last_assistant_message"] = answer[:ANSWER_CHARS]

    # End of a turn is the first moment the rollout holds a complete answer, and
    # SessionEnd is the last chance to read it. The totals are absolute, so a
    # re-read simply restates them.
    if event in ("Stop", "Interrupt", "SessionEnd") and raw.get("transcript_path"):
        stats = transcript_usage(raw["transcript_path"], raw.get("model"))
        if stats:
            body["stats"] = stats
        samples = context_samples(raw["transcript_path"])
        if samples:
            body["context_samples"] = samples

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
    reachable at the URL the hook will post to?"""
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
