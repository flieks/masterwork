"""Apply one proposed file change dict to an already root-validated path.

Shared by proposal accept and simulation suggestion apply — both store changes
as {path, action, new_content, description} dicts and validate paths against
the provider roots before calling this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def apply_change(change: dict[str, Any], resolved: Path) -> None:
    action = change.get("action")
    if action in ("update", "create"):
        content = change.get("new_content")
        if content is None:
            raise ValueError(f"missing new_content for {action}: {resolved}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    elif action == "delete":
        resolved.unlink()
    else:
        raise ValueError(f"unknown action: {action!r}")
