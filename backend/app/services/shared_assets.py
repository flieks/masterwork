"""Which OTHER projects link each asset.

Injected into prompts that may propose edits to shared assets, so the model
knows an asset is load-bearing for goals it cannot see from this project alone.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import projects as project_repo
from app.services.redact import redact


def _excerpt(goal: str) -> str:
    text = " ".join(goal.split())
    return text[:117] + "..." if len(text) > 120 else text


async def shared_asset_notes(db: AsyncSession, *, exclude_project_id: uuid.UUID) -> dict[str, str]:
    """asset_id -> "'Name' (goal: ...); 'Name2' (goal: ...)" across OTHER projects."""
    notes: dict[str, list[str]] = {}
    for project in await project_repo.list_projects(db):
        if project.id == exclude_project_id:
            continue
        label = redact(f"'{project.name}' (goal: {_excerpt(project.goal or '(empty)')})")
        for asset_id in project.asset_ids:
            notes.setdefault(asset_id, []).append(label)
    return {asset_id: "; ".join(labels) for asset_id, labels in notes.items()}
