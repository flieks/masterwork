"""Write-boundary globs: `*` never crosses `/`, `**` may."""

from __future__ import annotations

import pytest
from adw.gates import matches_boundary, normalize_path, translate_glob


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        # A single star must not swallow a directory separator.
        ("factory/adw/*.py", "factory/adw/gates.py", True),
        ("factory/adw/*.py", "factory/adw/sub/x/y.py", False),
        ("factory/adw/*.py", "factory/adw/nested/gates.py", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("plan.md", "plan.md", True),
        ("plan.md", "plan.md.bak", False),
        ("plan.md", "sub/plan.md", False),
        # `**` may cross separators, and `**/` also matches zero directories.
        ("docs/**", "docs/a.md", True),
        ("docs/**", "docs/deep/nested/a.md", True),
        ("docs/**", "docs", False),
        ("docs/**", "docsite/a.md", False),
        ("docs/**", "adocs/a.md", False),
        ("**/*.py", "main.py", True),
        ("**/*.py", "app/api/main.py", True),
        ("**/*.py", "app/api/main.ts", False),
        ("docs/specs/**", "docs/specs/2026/plan.md", True),
        ("docs/specs/**", "docs/other/plan.md", False),
        ("tests/**/*_test.py", "tests/a_test.py", True),
        ("tests/**/*_test.py", "tests/unit/deep/a_test.py", True),
        ("tests/**/*_test.py", "tests/unit/a.py", False),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("src/?.py", "src/a/b.py", False),
        # Regex metacharacters in a pattern are literals.
        ("a+b.md", "a+b.md", True),
        ("a+b.md", "aab.md", False),
    ],
)
def test_translate_glob(pattern: str, path: str, expected: bool):
    assert matches_boundary(path, [pattern]) is expected


def test_boundary_is_any_of_the_patterns():
    boundary = ["docs/**", "README.md", "CHANGELOG.md"]
    assert matches_boundary("docs/guide/index.md", boundary)
    assert matches_boundary("CHANGELOG.md", boundary)
    assert not matches_boundary("app/main.py", boundary)


def test_empty_boundary_matches_nothing():
    assert not matches_boundary("README.md", [])


def test_leading_dot_slash_is_stripped_but_dotfiles_survive():
    assert normalize_path("./app/main.py") == "app/main.py"
    assert normalize_path(".env") == ".env"
    assert matches_boundary("./docs/a.md", ["docs/**"])
    assert not matches_boundary(".env", ["env"])


def test_translate_glob_output_is_a_regex_fragment():
    assert translate_glob("docs/**") == "docs/.*"
    assert translate_glob("*.md") == "[^/]*\\.md"
    assert translate_glob("**/x.py") == "(?:.*/)?x\\.py"
