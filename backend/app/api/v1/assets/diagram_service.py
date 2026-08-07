"""Asset diagram business logic: read a cached diagram, or generate one via a
one-shot ``claude -p`` that reads the asset file and returns a Mermaid flowchart.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets import service as asset_service
from app.api.v1.assets.schemas import AssetDiagram
from app.core.exceptions import DiagramGenerationError, DiagramNotFoundError
from app.providers.base import Provider, ScannedAsset
from app.repositories import diagrams as diagram_repo
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
from app.services.mermaid_parser import extract_mermaid
from app.services.redact import redact


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _file_hash(asset: ScannedAsset) -> str:
    """sha256 of the asset file's current bytes. Used both at generation time
    (stored) and at read time (compared) so `stale` is representation-invariant.
    """
    try:
        raw = asset.path.read_bytes()
    except OSError:
        # File vanished between scan and hash: fall back to the scanned content.
        raw = asset.content.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _diagram_prompt(asset: ScannedAsset) -> str:
    # redact(): path and name are file-system-derived and could carry secrets.
    return redact(
        f"Read the file at {asset.path} — it is a Claude Code {asset.kind} "
        f'named "{asset.name}".\n\n'
        f"Then produce a Mermaid flowchart that explains how this {asset.kind} works "
        "internally: its trigger conditions, the main steps and decisions it takes, and "
        "its outputs.\n\n"
        "Reply with ONLY a single fenced code block whose info string is `mermaid` — no "
        "prose before or after it. Use `flowchart TD`. Put every node label in double "
        'quotes so special characters survive, e.g. A["Read the asset file"].'
    )


async def get_diagram(db: AsyncSession, providers: list[Provider], asset_id: str) -> AssetDiagram:
    asset = asset_service.find_asset(providers, asset_id)  # 400 malformed / 404 unknown
    row = await diagram_repo.get_diagram(db, asset_id)
    if row is None:
        raise DiagramNotFoundError(f"no diagram generated for asset: {asset_id}")
    return AssetDiagram(
        asset_id=asset_id,
        mermaid=row.mermaid,
        generated_at=row.generated_at,
        stale=row.file_hash != _file_hash(asset),
    )


async def generate_diagram(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    asset_id: str,
) -> AssetDiagram:
    asset = asset_service.find_asset(providers, asset_id)  # 400 malformed / 404 unknown
    file_hash = _file_hash(asset)

    try:
        reply = await runner.run_once(_diagram_prompt(asset))
    except ClaudeRunnerError as exc:
        raise DiagramGenerationError(f"claude failed to generate a diagram: {exc}") from exc

    mermaid = extract_mermaid(reply)
    if mermaid is None:
        raise DiagramGenerationError("the assistant did not return a mermaid block")

    generated_at = _utcnow()
    await diagram_repo.upsert_diagram(
        db,
        asset_id=asset_id,
        file_hash=file_hash,
        mermaid=mermaid,
        generated_at=generated_at,
    )
    await db.commit()
    return AssetDiagram(asset_id=asset_id, mermaid=mermaid, generated_at=generated_at, stale=False)
