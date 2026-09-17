#!/usr/bin/env python3
"""Stand-in for `codex exec --json`: canned JSONL events plus scripted side effects.

Tests put a copy of this on PATH as `codex`. FACTORY_FAKE_CODEX_SCRIPT points at a
JSON file of the shape::

    {"invocations": [{"envelope": {...}, "write_files": {"app.py": "..."}}, ...],
     "default": {...}}

The event shapes are the ones codex-cli 0.153.4 printed for real: `thread.started`,
`turn.started`, `item.started`/`item.completed` (command_execution, file_change,
agent_message, error), `turn.completed` with usage, and `error` + `turn.failed`.
Every invocation is logged to FACTORY_FAKE_CODEX_LOG with its argv, cwd, the
MASTERWORK_* env, and whether stdin was /dev/null (a real `codex exec` hangs otherwise).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _flag_values(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == flag]


def _stdin_is_devnull() -> bool:
    try:
        return os.path.samestat(os.fstat(0), os.stat(os.devnull))
    except OSError:
        return False


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _reply_text(spec: dict) -> str:
    if "raw_reply" in spec:
        return str(spec["raw_reply"])
    parts = [str(spec.get("text", "Work done."))]
    envelope = spec.get("envelope")
    if envelope is not None:
        parts.append("```json\n" + json.dumps(envelope, indent=2) + "\n```")
    return "\n\n".join(parts)


def main() -> int:
    argv = sys.argv[1:]
    script = Path(os.environ["FACTORY_FAKE_CODEX_SCRIPT"])
    state = Path(os.environ["FACTORY_FAKE_CODEX_STATE"])
    index = int(state.read_text()) if state.is_file() else 0
    state.write_text(str(index + 1))
    data = json.loads(script.read_text(encoding="utf-8"))
    invocations = data.get("invocations") or []
    spec = invocations[index] if index < len(invocations) else data.get("default")

    resume = argv[2] if argv[:2] == ["exec", "resume"] else None
    prompt = argv[argv.index("--") + 1] if "--" in argv else ""
    cwd = Path.cwd()
    with Path(os.environ["FACTORY_FAKE_CODEX_LOG"]).open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "n": index,
                    "argv": argv,
                    "cwd": str(cwd),
                    "resume": resume,
                    "prompt": prompt,
                    "configs": _flag_values(argv, "-c"),
                    "stdin_devnull": _stdin_is_devnull(),
                    "env": {k: v for k, v in os.environ.items() if k.startswith("MASTERWORK_")},
                }
            )
            + "\n"
        )
    if spec is None:
        print(f"fake codex: no scripted invocation #{index}", file=sys.stderr)
        return 3

    thread_id = resume or spec.get("thread_id") or f"fake-thread-{index}"
    for line in spec.get("noise") or []:
        sys.stdout.write(str(line) + "\n")
    _emit({"type": "thread.started", "thread_id": thread_id})
    _emit({"type": "turn.started"})
    time.sleep(float(spec.get("sleep_seconds", 0)))

    items = 0
    for command in spec.get("commands") or []:
        item = {
            "id": f"item_{items}",
            "type": "command_execution",
            "command": f"/bin/zsh -lc '{command['command']}'",
            "aggregated_output": "",
            "exit_code": None,
            "status": "in_progress",
        }
        _emit({"type": "item.started", "item": item})
        exit_code = int(command.get("exit_code", 0))
        done = dict(item, exit_code=exit_code, status="completed" if exit_code == 0 else "failed")
        _emit({"type": "item.completed", "item": done})
        items += 1

    writes = spec.get("write_files") or {}
    for rel, content in writes.items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        change = {"path": str(target), "kind": "update" if existed else "add"}
        item = {"id": f"item_{items}", "type": "file_change", "changes": [change]}
        _emit({"type": "item.started", "item": dict(item, status="in_progress")})
        target.write_text(str(content), encoding="utf-8")
        _emit({"type": "item.completed", "item": dict(item, status="completed")})
        items += 1

    for message in spec.get("errors") or []:
        _emit({"type": "error", "message": message})

    if spec.get("fail"):
        error = {"message": json.dumps({"type": "error", "error": {"message": spec["fail"]}})}
        _emit({"type": "turn.failed", "error": error})
        return int(spec.get("exit_code", 1))

    for text in [*(spec.get("messages") or []), _reply_text(spec)]:
        _emit(
            {
                "type": "item.completed",
                "item": {"id": f"item_{items}", "type": "agent_message", "text": text},
            }
        )
        items += 1
    _emit(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": int(spec.get("input_tokens", 1000)),
                "cached_input_tokens": int(spec.get("cached_input_tokens", 400)),
                "cache_write_input_tokens": 0,
                "output_tokens": int(spec.get("output_tokens", 200)),
                "reasoning_output_tokens": int(spec.get("reasoning_output_tokens", 50)),
            },
        }
    )
    return int(spec.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
