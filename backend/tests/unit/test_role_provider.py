"""MasterworkRoleProvider: the factory's role store scanned as editable assets."""

from __future__ import annotations

import json
from pathlib import Path

from app.api.v1.assets.service import parse_asset_id
from app.providers.masterwork_roles import MasterworkRoleProvider, read_role_config


EXPECTED_IDS = {
    "masterwork:agent:conventions",
    "masterwork:agent:plan:system",
    "masterwork:agent:plan:user",
    "masterwork:agent:plan:config",
    "masterwork:agent:build:system",
    "masterwork:agent:build:user",
    # no build:config — the fixture's build role has no role.json
}


def test_scan_yields_one_asset_per_file(role_tree: Path) -> None:
    assets = {a.id: a for a in MasterworkRoleProvider(store_root=role_tree).scan()}

    assert set(assets) == EXPECTED_IDS
    system = assets["masterwork:agent:plan:system"]
    assert system.provider == "masterwork"
    assert system.kind == "agent"
    assert system.name == "plan:system"
    assert system.title == "plan · system prompt"
    assert system.description.startswith("Turns a request into an implementation plan. —")
    assert system.model == "opus"  # from role.json
    assert system.read_only is False
    assert system.path == role_tree / "plan" / "system.md"
    assert "PLAN stage" in system.content


def test_role_json_is_a_read_only_config_asset(role_tree: Path) -> None:
    provider = MasterworkRoleProvider(store_root=role_tree)
    assets = {a.id: a for a in provider.scan()}
    config = assets["masterwork:agent:plan:config"]
    assert config.read_only is True
    assert config.title == "plan · config"
    assert '"writes"' in config.content
    assert config.description.startswith("Turns a request into an implementation plan. —")
    assert provider.asset_id_for_path(role_tree / "plan" / "role.json") == (
        "masterwork:agent:plan:config"
    )


def test_conventions_are_a_writable_asset(role_tree: Path) -> None:
    provider = MasterworkRoleProvider(store_root=role_tree)
    assets = {a.id: a for a in provider.scan()}
    conventions = assets["masterwork:agent:conventions"]
    assert conventions.read_only is False
    assert conventions.title == "conventions · shared rules"
    assert "Never hardcode a secret" in conventions.content
    assert provider.asset_id_for_path(role_tree / "conventions.md") == (
        "masterwork:agent:conventions"
    )


def test_missing_conventions_is_no_asset(role_tree: Path) -> None:
    (role_tree / "conventions.md").unlink()
    ids = {a.id for a in MasterworkRoleProvider(store_root=role_tree).scan()}
    assert "masterwork:agent:conventions" not in ids


def test_user_template_describes_itself(role_tree: Path) -> None:
    assets = {a.id: a for a in MasterworkRoleProvider(store_root=role_tree).scan()}
    user = assets["masterwork:agent:plan:user"]
    assert user.title == "plan · task template"
    assert "{{placeholders}}" in user.description
    assert "{{request}}" in user.content


def test_missing_role_json_degrades(role_tree: Path) -> None:
    assets = {a.id: a for a in MasterworkRoleProvider(store_root=role_tree).scan()}
    build = assets["masterwork:agent:build:system"]
    assert build.model is None
    assert build.description == "Static identity, sent as the system prompt on every turn."


def test_absent_store_is_zero_assets_not_an_error(tmp_path: Path) -> None:
    provider = MasterworkRoleProvider(store_root=tmp_path / "never-seeded")
    assert list(provider.scan()) == []
    assert provider.roots() == [tmp_path / "never-seeded"]  # still writable: can be created


def test_malformed_role_json_does_not_break_the_scan(role_tree: Path) -> None:
    (role_tree / "plan" / "role.json").write_text("{not json", encoding="utf-8")
    assets = {a.id: a for a in MasterworkRoleProvider(store_root=role_tree).scan()}
    assert set(assets) == EXPECTED_IDS  # the config asset survives, verbatim
    assert assets["masterwork:agent:plan:system"].model is None
    assert read_role_config(role_tree / "plan").purpose == ""

    (role_tree / "plan" / "role.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert read_role_config(role_tree / "plan").model is None


def test_partial_role_yields_only_what_exists(role_tree: Path) -> None:
    (role_tree / "build" / "user.md").unlink()
    ids = {a.id for a in MasterworkRoleProvider(store_root=role_tree).scan()}
    assert "masterwork:agent:build:system" in ids
    assert "masterwork:agent:build:user" not in ids


def test_weird_role_names_are_skipped_not_mangled(role_tree: Path) -> None:
    # A colon would break the id round-trip; uppercase would alias itself on a
    # case-insensitive filesystem. Both are skipped rather than rewritten.
    for bad in ("with:colon", "has space", "Review", ".hidden", "-leading-dash"):
        directory = role_tree / bad
        directory.mkdir()
        (directory / "system.md").write_text("x", encoding="utf-8")
    provider = MasterworkRoleProvider(store_root=role_tree)

    assert {f"masterwork:agent:{a.name}" for a in provider.scan()} == EXPECTED_IDS
    assert provider.asset_id_for_path(role_tree / "with:colon" / "system.md") is None
    assert provider.asset_id_for_path(role_tree / "Review" / "system.md") is None


def test_hyphen_and_underscore_names_are_legal(role_tree: Path) -> None:
    directory = role_tree / "review_2-fast"
    directory.mkdir()
    (directory / "user.md").write_text("go\n", encoding="utf-8")
    provider = MasterworkRoleProvider(store_root=role_tree)
    assert "masterwork:agent:review_2-fast:user" in {a.id for a in provider.scan()}


def test_stray_files_and_nested_dirs_are_not_assets(role_tree: Path) -> None:
    (role_tree / "README.md").write_text("not a role", encoding="utf-8")
    nested = role_tree / "plan" / "examples"
    nested.mkdir()
    (nested / "system.md").write_text("decoy", encoding="utf-8")
    provider = MasterworkRoleProvider(store_root=role_tree)
    assert {a.id for a in provider.scan()} == EXPECTED_IDS
    assert provider.asset_id_for_path(nested / "system.md") is None
    assert provider.asset_id_for_path(role_tree / "README.md") is None


def test_asset_id_round_trips_through_parse_asset_id(role_tree: Path) -> None:
    provider = MasterworkRoleProvider(store_root=role_tree)
    for role, part, filename in (
        ("plan", "system", "system.md"),
        ("build", "user", "user.md"),
        ("plan", "config", "role.json"),
    ):
        asset_id = provider.asset_id_for_path(role_tree / role / filename)
        assert asset_id == f"masterwork:agent:{role}:{part}"
        assert parse_asset_id(asset_id) == ("masterwork", "agent", f"{role}:{part}")
    conventions_id = provider.asset_id_for_path(role_tree / "conventions.md")
    assert conventions_id == "masterwork:agent:conventions"
    assert parse_asset_id(conventions_id) == ("masterwork", "agent", "conventions")


def test_ids_never_collide_with_the_claude_providers(role_tree: Path) -> None:
    providers = [a.id.split(":", 1)[0] for a in MasterworkRoleProvider(store_root=role_tree).scan()]
    assert set(providers) == {"masterwork"}
    assert not any(p.startswith("claude") for p in providers)
