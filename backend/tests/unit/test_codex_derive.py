"""Codex's hook vocabulary, read onto the same turn/lane shape Claude Code's is.

Pure functions, no database: `derive` decides what an event means for the
run's lanes and stages, `assets` which skill or subagent it names.
"""

from __future__ import annotations

from app.api.v1.coding import assets, derive
from app.db.models.coding import (
    ASSET_AGENT,
    ASSET_SKILL,
    MAIN_AGENT,
    PHASE_ABANDONED,
    PHASE_PASSED,
    UNKNOWN_AGENT,
    USE_SKILL_READ,
    USE_SPAWN_CALL,
    USE_SUBAGENT_STOP,
)

# ------------------------------------------------------------------ derive ---


def test_a_subagent_start_opens_a_span_on_the_agents_own_lane() -> None:
    derived = derive.from_event(
        "SubagentStart", None, {"agent_type": "spec_review", "agent_id": "a1"}
    )
    assert derived.lane == "spec_review"
    assert derived.opens_turn is True
    assert derived.turn_label == "spec_review"
    assert [a.name for a in derived.agents] == ["spec_review"]


def test_a_subagent_start_that_names_nothing_still_opens_a_lane() -> None:
    derived = derive.from_event("SubagentStart", None, {"agent_id": "a1"})
    assert derived.lane == UNKNOWN_AGENT
    assert derived.opens_turn is True


def test_a_subagent_stop_closes_it_and_counts_the_turn() -> None:
    derived = derive.from_event("SubagentStop", None, {"agent_type": "spec_review"})
    assert derived.lane == "spec_review"
    assert derived.closes_turn is True
    assert derived.close_status == PHASE_PASSED
    assert derived.agents[0].add_turns == 1


def test_an_interrupt_closes_the_main_turn_as_unfinished() -> None:
    derived = derive.from_event("Interrupt", None, {"turn_id": "t1"})
    assert derived.lane == MAIN_AGENT
    assert derived.closes_turn is True
    assert derived.close_status == PHASE_ABANDONED
    # It was a turn, cut short — counted like a Stop is.
    assert derived.agents[0].add_turns == 1


def test_a_stop_still_closes_the_turn_as_passed() -> None:
    derived = derive.from_event("Stop", None, {"turn_id": "t1"})
    assert derived.closes_turn is True
    assert derived.close_status == PHASE_PASSED


def test_an_event_with_no_claude_equivalent_is_kept_and_lands_in_no_lane() -> None:
    for name in ("PermissionRequest", "PreCompact", "PostCompact"):
        derived = derive.from_event(name, None, {"turn_id": "t1"})
        assert derived.lane is None, name
        assert derived.opens_turn is False and derived.closes_turn is False, name
        assert derived.phase is None, name


def test_a_codex_shell_call_belongs_to_main_like_any_tool_call() -> None:
    derived = derive.from_event(
        "PostToolUse", "exec_command", {"tool_input": {"cmd": "ls", "workdir": "/repo"}}
    )
    assert derived.lane == MAIN_AGENT


def test_the_title_marker_is_read_off_codexs_command_key() -> None:
    payload = {"tool_input": {"cmd": 'echo "masterwork:title=Wire Codex up"'}}
    assert derive.marker_title(payload) == "Wire Codex up"
    derived = derive.from_event("PostToolUse", "exec_command", payload)
    assert derived.title == "Wire Codex up"


def test_the_title_marker_is_read_off_an_argv_list() -> None:
    payload = {"tool_input": {"command": ["bash", "-lc", "echo masterwork:title=From a list"]}}
    assert derive.marker_title(payload) == "From a list"


# ------------------------------------------------------------------ assets ---

SHELL_ROOTS = [
    "/Users/dev/.agents/skills/code-review/SKILL.md",
    "/Users/dev/.codex/skills/code-review/SKILL.md",
    "/Users/dev/.claude/skills/code-review/SKILL.md",
    "/repo/.claude/skills/code-review/SKILL.md",
]


def _exec(cmd: str, tool: str = "exec_command") -> list[assets.AssetUse]:
    return assets.from_event("PostToolUse", tool, {"tool_input": {"cmd": cmd}}, lane=MAIN_AGENT)


def test_a_shell_command_printing_a_skill_under_any_root_is_a_skill_read() -> None:
    for path in SHELL_ROOTS:
        (use,) = _exec(f"sed -n '1,240p' {path}")
        assert use.kind == ASSET_SKILL, path
        assert use.name == "code-review", path
        assert use.source == USE_SKILL_READ, path
        assert use.input == {"path": path}, path


def test_the_skill_path_is_found_mid_command_and_in_quotes() -> None:
    (use,) = _exec('cat "/Users/dev/.agents/skills/tdd/SKILL.md" && pwd')
    assert use.name == "tdd"
    assert use.input == {"path": "/Users/dev/.agents/skills/tdd/SKILL.md"}
    (use,) = _exec("cat /Users/dev/.codex/skills/tdd/SKILL.md; ls")
    assert use.name == "tdd"


def test_the_exec_custom_tool_carries_its_command_inside_a_script() -> None:
    script = (
        'const r = await tools.exec_command({"cmd":"sed -n \'1,240p\' '
        '/Users/dev/.agents/skills/grilling/SKILL.md","workdir":"/repo"}); text(r.output);'
    )
    (use,) = assets.from_event("PostToolUse", "exec", {"tool_input": {"input": script}}, lane=None)
    assert use.name == "grilling"


def test_an_argv_list_command_is_read_too() -> None:
    (use,) = assets.from_event(
        "PostToolUse",
        "shell",
        {"tool_input": {"command": ["bash", "-lc", "cat /Users/dev/.codex/skills/tdd/SKILL.md"]}},
        lane=MAIN_AGENT,
    )
    assert use.name == "tdd"


def test_a_shell_command_naming_no_skill_is_nothing() -> None:
    assert _exec("cat README.md") == []
    assert _exec("cat /Users/dev/.agents/skills/tdd/SKILL.md.bak") == []
    assert _exec("ls /Users/dev/.agents/skills") == []


def test_a_claude_bash_call_is_deliberately_not_read_this_way() -> None:
    assert _exec("cat /Users/dev/.claude/skills/tdd/SKILL.md", tool="Bash") == []


def test_a_denied_shell_call_is_not_a_use() -> None:
    uses = assets.from_event(
        "PreToolUse",
        "exec_command",
        {"tool_input": {"cmd": "cat /Users/dev/.agents/skills/tdd/SKILL.md"}},
        lane=MAIN_AGENT,
    )
    assert uses == []


def test_a_read_of_a_skill_under_the_shared_root_counts_for_claude_too() -> None:
    (use,) = assets.from_event(
        "PostToolUse",
        "Read",
        {"tool_input": {"file_path": "/Users/dev/.agents/skills/tdd/SKILL.md"}},
        lane=MAIN_AGENT,
    )
    assert (use.kind, use.name, use.source) == (ASSET_SKILL, "tdd", USE_SKILL_READ)


def test_a_subagent_start_records_the_spawn_with_what_codex_said() -> None:
    (use,) = assets.from_event(
        "SubagentStart",
        None,
        {"agent_type": "spec_review", "agent_id": "a1"},
        lane=MAIN_AGENT,
    )
    assert (use.kind, use.name, use.source) == (ASSET_AGENT, "spec_review", USE_SPAWN_CALL)
    assert use.input == {"agent_type": "spec_review", "agent_id": "a1"}

    (stop,) = assets.from_event("SubagentStop", None, {"agent_type": "spec_review"}, lane=None)
    assert (stop.name, stop.source, stop.input) == ("spec_review", USE_SUBAGENT_STOP, None)


def test_command_text_reads_every_key_a_shell_tool_uses() -> None:
    assert assets.command_text({"cmd": "ls"}) == "ls"
    assert assets.command_text({"command": ["bash", "-lc", "ls"]}) == "bash -lc ls"
    assert assets.command_text({"input": "tools.exec_command({cmd: 'ls'})"}).startswith("tools.")
    assert assets.command_text({"workdir": "/repo"}) is None
