"""Title precedence: which signal is allowed to overwrite a stored title.

Ranked in `service._TITLE_RANK`, which does not hold every TITLE_* constant —
`cwd` is derived at read time and never stored — so the lookup has to tolerate a
source it has never heard of.
"""

from __future__ import annotations

from app.api.v1.coding.derive import from_event, marker_title
from app.api.v1.coding.service import _set_title
from app.db.models.coding import (
    TITLE_CWD,
    TITLE_FACTORY,
    TITLE_PROMPT,
    TITLE_PROVENANCE,
    TITLE_SUMMARY,
    CodingSession,
)


def _session() -> CodingSession:
    return CodingSession(id="s1", cwd="/repo")


def _echo(command: str) -> dict[str, object]:
    return {"tool_input": {"command": command}}


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


def test_an_echoed_summary_replaces_the_prompt_it_summarises() -> None:
    session = _session()
    _set_title(session, "make the session cards readable and…", TITLE_PROMPT)
    _set_title(session, "Summarise first prompts into session titles", TITLE_SUMMARY)
    assert session.title == "Summarise first prompts into session titles"


def test_a_second_task_in_one_session_does_not_rename_it() -> None:
    session = _session()
    _set_title(session, "First task", TITLE_SUMMARY)
    _set_title(session, "Second task", TITLE_SUMMARY)
    assert session.title == "First task"


def test_a_stage_child_keeps_its_provenance_name_over_an_echoed_summary() -> None:
    session = _session()
    _set_title(session, "build stage · factory-abc", TITLE_PROVENANCE)
    _set_title(session, "Add a titlecase helper", TITLE_SUMMARY)
    assert session.title == "build stage · factory-abc"


# The marker itself: what an agent has to echo for any of the above to fire.


def test_a_marker_in_a_shell_command_becomes_the_title() -> None:
    derived = from_event(
        "PostToolUse", "Bash", _echo('echo "masterwork:title=Give sessions a real title"')
    )
    assert (derived.title, derived.title_source) == ("Give sessions a real title", TITLE_SUMMARY)


def test_the_router_can_announce_its_verdict_and_the_title_in_one_call() -> None:
    command = (
        'echo "masterwork:route=chat -- taste calls"\n'
        'echo "masterwork:title=Give sessions a real title"'
    )
    assert marker_title(_echo(command)) == "Give sessions a real title"


def test_the_sentence_describing_the_marker_is_not_a_title() -> None:
    """The skill file that documents this gets read and grepped inside sessions
    that use it; only the `=` form is a claim about the run."""
    assert marker_title(_echo('grep -rn "masterwork:title" ~/.claude')) is None
    assert marker_title(_echo('echo "masterwork:title="')) is None
    assert marker_title(_echo("ls")) is None
    assert marker_title({"tool_response": {"stdout": "masterwork:title=not from here"}}) is None
