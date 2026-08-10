"""Title precedence: which signal is allowed to overwrite a stored title.

Ranked in `service._TITLE_RANK`, which does not hold every TITLE_* constant —
`cwd` is derived at read time and never stored — so the lookup has to tolerate a
source it has never heard of.
"""

from __future__ import annotations

from app.api.v1.coding.service import _set_title
from app.db.models.coding import TITLE_CWD, TITLE_FACTORY, TITLE_PROMPT, CodingSession


def _session() -> CodingSession:
    return CodingSession(id="s1", cwd="/repo")


def test_a_stronger_source_replaces_a_stored_title() -> None:
    session = _session()
    _set_title(session, "the prompt", TITLE_PROMPT)
    _set_title(session, "the request", TITLE_FACTORY)
    assert (session.title, session.title_source) == ("the request", TITLE_FACTORY)


def test_an_equal_source_never_replaces_the_first_title() -> None:
    session = _session()
    _set_title(session, "one", TITLE_PROMPT)
    _set_title(session, "two", TITLE_PROMPT)
    assert session.title == "one"


def test_an_unranked_source_is_the_weakest_rather_than_a_crash() -> None:
    """`cwd` has no rank, and neither does a signal nobody has written yet. The
    sibling lookup on the same line already defaulted; this one used to raise."""
    session = _session()
    _set_title(session, "the prompt", TITLE_PROMPT)
    _set_title(session, "repo", TITLE_CWD)
    assert (session.title, session.title_source) == ("the prompt", TITLE_PROMPT)


def test_an_unranked_source_still_titles_an_untitled_run() -> None:
    session = _session()
    _set_title(session, "repo", TITLE_CWD)
    assert (session.title, session.title_source) == ("repo", TITLE_CWD)
