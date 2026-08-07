"""Cached per-asset Mermaid diagrams.

Keyed by asset id (a slug, not a DB uuid). We store the file's sha256 at
generation time so `stale` can be computed by comparing against the current
file hash. Regeneration overwrites the row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetDiagram(Base):
    __tablename__ = "asset_diagrams"

    asset_id: Mapped[str] = mapped_column(String, primary_key=True)
    # sha256 of the asset file at generation time; drives the `stale` flag.
    file_hash: Mapped[str] = mapped_column(String)
    mermaid: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
