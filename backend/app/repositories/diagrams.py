"""Data access for cached asset diagrams."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.diagram import AssetDiagram


async def get_diagram(db: AsyncSession, asset_id: str) -> AssetDiagram | None:
    return await db.get(AssetDiagram, asset_id)


async def upsert_diagram(
    db: AsyncSession,
    *,
    asset_id: str,
    file_hash: str,
    mermaid: str,
    generated_at: datetime,
) -> AssetDiagram:
    row = await db.get(AssetDiagram, asset_id)
    if row is None:
        row = AssetDiagram(
            asset_id=asset_id, file_hash=file_hash, mermaid=mermaid, generated_at=generated_at
        )
        db.add(row)
    else:
        row.file_hash = file_hash
        row.mermaid = mermaid
        row.generated_at = generated_at
    await db.flush()
    await db.refresh(row)
    return row
