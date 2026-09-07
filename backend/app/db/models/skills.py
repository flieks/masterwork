"""Installed community skills — what lets uninstall tell an installed catalog
skill from a hand-written one, and never delete a directory it did not write."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class InstalledSkill(Base):
    """One skill masterwork installed from the community catalog, keyed on the
    on-disk directory name (the slug)."""

    __tablename__ = "installed_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    owner: Mapped[str] = mapped_column(String(200))
    repo: Mapped[str] = mapped_column(String(200))
    # Null means all rights reserved, not "not looked up" — see skill_catalog.resolve_license.
    license: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Attribute renamed off "registry": DeclarativeBase already owns that name.
    source_registry: Mapped[str] = mapped_column("registry", String(50))
    installed_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("name", name="uq_installed_skills_name"),)
