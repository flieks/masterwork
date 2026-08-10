#!/usr/bin/env python3
"""Stand-in for the `claude` CLI: canned stream-json plus scripted side effects.

Tests put a copy of this on PATH as `claude`. FACTORY_FAKE_SCRIPT points at a JSON
file of the shape::

    {"invocations": [{"envelope": {...}, "write_files": {"plan.md": "..."}}, ...],
     "default": {...}}

Every invocation is appended to FACTORY_FAKE_LOG so tests can assert on the argv
(``--resume``, ``--disallowedTools``, the prompt text) and on the environment the
child was actually handed (``env`` = the MASTERWORK_* vars, ``path`` = inheritance).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _state_path(script: Path) -> Path:
    return Path(os.environ.get("FACTORY_FAKE_STATE") or f"{script}.state")


def _log_path(script: Path) -> Path:
    return Path(os.environ.get("FACTORY_FAKE_LOG") or f"{script}.calls.jsonl")


def _next_index(state: Path) -> int:
    current = int(state.read_text()) if state.is_file() else 0
    state.write_text(str(current + 1))
    return current


def _arg_value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def _reply_text(spec: dict) -> str:
    if "raw_reply" in spec:
        return str(spec["raw_reply"])
    parts = [str(spec.get("text", "Work done."))]
    envelope = spec.get("envelope")
    if envelope is not None:
        parts.append("```json\n" + json.dumps(envelope, indent=2) + "\n```")
    if spec.get("trailing"):
        parts.append(str(spec["trailing"]))
    return "\n\n".join(parts)


def _apply_side_effects(spec: dict, cwd: Path) -> None:
    for rel, content in (spec.get("write_files") or {}).items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
    for rel in spec.get("delete_files") or []:
        target = cwd / rel
        if target.is_file():
            target.unlink()


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    argv = sys.argv[1:]
    script_env = os.environ.get("FACTORY_FAKE_SCRIPT")
    if not script_env:
        print("FACTORY_FAKE_SCRIPT is not set", file=sys.stderr)
        return 3
    script = Path(script_env)
    data = json.loads(script.read_text(encoding="utf-8"))
    index = _next_index(_state_path(script))
    invocations = data.get("invocations") or []
    spec = invocations[index] if index < len(invocations) else data.get("default")

    cwd = Path.cwd()
    prompt = _arg_value(argv, "-p") or ""
    resume = _arg_value(argv, "--resume")
    with _log_path(script).open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "n": index,
                    "argv": argv,
                    "cwd": str(cwd),
                    "resume": resume,
                    "prompt": prompt,
                    "env": {k: v for k, v in os.environ.items() if k.startswith("MASTERWORK_")},
                    "path": os.environ.get("PATH", ""),
                }
            )
            + "\n"
        )

    if spec is None:
        print(f"fake claude: no scripted invocation #{index}", file=sys.stderr)
        return 3

    session_id = resume or spec.get("session_id") or f"fake-session-{index}"
    _emit({"type": "system", "subtype": "init", "session_id": session_id, "cwd": str(cwd)})
    # A turn that does not come back on its own — how a hung run is reproduced.
    time.sleep(float(spec.get("sleep_seconds", 0)))

    for tool in spec.get("tools") or [{"name": "Write", "input": {"file_path": "<scripted>"}}]:
        _emit(
            {
                "type": "assistant",
                "session_id": session_id,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t0",
                            "name": tool["name"],
                            "input": tool.get("input", {}),
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            }
        )
        _emit(
            {
                "type": "user",
                "session_id": session_id,
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "ok"}]
                },
            }
        )

    _apply_side_effects(spec, cwd)
    reply = _reply_text(spec)
    _emit(
        {
            "type": "assistant",
            "session_id": session_id,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 30,
                    # The prompt the last message carried — what a context bar reads.
                    "cache_read_input_tokens": int(spec.get("cache_read_tokens", 0)),
                },
            },
        }
    )
    _emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": bool(spec.get("is_error")),
            "result": reply,
            "session_id": session_id,
            "duration_ms": int(spec.get("duration_ms", 1200)),
            "num_turns": 2,
            "total_cost_usd": float(spec.get("cost_usd", 0.01)),
            "usage": {
                "input_tokens": int(spec.get("input_tokens", 1000)),
                "output_tokens": int(spec.get("output_tokens", 200)),
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "modelUsage": {"fake-model": {"inputTokens": 1000, "outputTokens": 200}},
        }
    )
    return int(spec.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
