"""SQLAlchemy declarative base and shared metadata."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import models so Alembic autogenerate and Base.metadata.create_all see them.
from app.db.models import chat as _chat  # noqa: E402,F401
from app.db.models import coding as _coding  # noqa: E402,F401
from app.db.models import diagram as _diagram  # noqa: E402,F401
from app.db.models import project as _project  # noqa: E402,F401
from app.db.models import simulation as _simulation  # noqa: E402,F401
