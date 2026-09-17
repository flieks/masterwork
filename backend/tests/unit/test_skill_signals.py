"""v1.49 skill signals in `coding.assets`: plugin-cache and `.system` paths, and
`$name` mentions matched against what is installed."""

from __future__ import annotations

import pytest

from app.api.v1.coding import assets

CODEX_PLUGIN = "/Users/me/.codex/plugins/cache/openai-bundled/chrome/latest"
CLAUDE_PLUGIN = "/Users/me/.claude/plugins/cache/official/vercel/1.0.0"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/Users/me/.codex/skills/.system/imagegen/SKILL.md", "imagegen"),
        (f"cat {CODEX_PLUGIN}/skills/browse/SKILL.md", "chrome:browse"),
        (f"{CLAUDE_PLUGIN}/skills/bootstrap/SKILL.md", "vercel:bootstrap"),
        ("/Users/me/.agents/skills/tdd/SKILL.md", "tdd"),
        ("/Users/me/.codex/plugins/cache/m/p/1/skills/x/README.md", None),
    ],
)
def test_skill_paths(text: str, expected: str | None) -> None:
    found = assets.skill_in(text)
    assert (found[0] if found else None) == expected


def test_the_first_path_in_a_command_wins() -> None:
    command = (
        "cat /a/.agents/skills/first/SKILL.md /a/.codex/plugins/cache/m/p/1/skills/second/SKILL.md"
    )
    assert assets.skill_in(command) == ("first", "/a/.agents/skills/first/SKILL.md")


def _prompt(text: str, names: frozenset[str] | None) -> list[str]:
    calls: list[int] = []

    def mentionable() -> frozenset[str]:
        calls.append(1)
        return names or frozenset()

    uses = assets.from_event(
        "UserPromptSubmit",
        None,
        {"prompt": text},
        lane="main",
        mentionable=None if names is None else mentionable,
    )
    if "$" not in text:
        assert calls == []  # the provider scan is never paid for a plain prompt
    return [u.name for u in uses]


def test_mentions_match_installed_names_only() -> None:
    names = frozenset({"deploy", "sites:publish", "tdd"})
    assert _prompt("run $deploy, then $sites:publish. $tdd! $nope", names) == [
        "deploy",
        "sites:publish",
        "tdd",
    ]


def test_money_and_glued_dollars_are_not_mentions() -> None:
    names = frozenset({"deploy"})
    assert _prompt("it costs $5 and a$deploy and $$deploy", names) == []


def test_no_lookup_without_a_dollar_or_without_a_lookup() -> None:
    assert _prompt("use the deploy skill", frozenset({"deploy"})) == []
    assert _prompt("use $deploy", None) == []
