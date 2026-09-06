"""Static checks on the LLM Bridge mod.

Paradox script has no compiler and fails silently: an unbalanced brace or a
misspelled localisation key produces a game that loads and quietly does nothing.
These are cheap guards against the failure modes that are invisible in-game, and
they run in CI so a broken mod cannot be pushed unnoticed.

They cannot check that a script *token* is real -- only the game knows that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoi4_harness.adapters.logtail import SCHEMA_VERSION

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"
SCRIPTS = sorted(MOD.rglob("*.txt"))


def strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_the_mod_ships_the_files_a_launcher_needs():
    assert (MOD / "descriptor.mod").is_file()
    assert (MOD.parent / "llm_bridge.mod").is_file()
    assert SCRIPTS, "no script files found"


def test_both_descriptors_agree():
    inner = (MOD / "descriptor.mod").read_text(encoding="utf-8")
    outer = (MOD.parent / "llm_bridge.mod").read_text(encoding="utf-8")
    assert inner == outer, "descriptor.mod and llm_bridge.mod have drifted apart"
    for key in ("name=", "version=", "supported_version=", "path="):
        assert key in inner, f"descriptor is missing {key}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_braces_balance(path):
    body = strip_comments(path.read_text(encoding="utf-8"))
    assert body.count("{") == body.count("}"), f"{path.name}: unbalanced braces"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_tabs_mixed_with_spaces_at_line_start(path):
    """Paradox tooling tolerates either, but mixing them makes diffs unreadable."""
    starts = {
        line[: len(line) - len(line.lstrip())][:1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and line[:1].isspace()
    }
    assert starts <= {"\t"}, f"{path.name}: indent with tabs, found {starts}"


def test_localisation_is_well_formed():
    loc = MOD / "localisation" / "english" / "llm_bridge_l_english.yml"
    lines = loc.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].strip() == "l_english:"
    for line in lines[1:]:
        if line.strip():
            assert re.fullmatch(r' \w+:\d+ ".*"', line), f"bad localisation line: {line!r}"


def test_every_decision_has_localisation():
    decisions = (MOD / "common" / "decisions" / "llm_bridge_decisions.txt").read_text(
        encoding="utf-8"
    )
    loc = (MOD / "localisation" / "english" / "llm_bridge_l_english.yml").read_text(
        encoding="utf-8-sig"
    )
    for name in re.findall(r"^\t(llmb_\w+) = \{", decisions, flags=re.M):
        assert f"{name}:0" in loc, f"decision {name} has no localisation"
        assert f"{name}_desc:0" in loc, f"decision {name} has no description"


def test_every_effect_the_decisions_call_exists():
    decisions = (MOD / "common" / "decisions" / "llm_bridge_decisions.txt").read_text(
        encoding="utf-8"
    )
    defined = set()
    for path in (MOD / "common" / "scripted_effects").glob("*.txt"):
        defined |= set(re.findall(r"^(\w+) = \{", path.read_text(encoding="utf-8"), flags=re.M))
    for called in re.findall(r"^\t{3}(\w+) = yes", decisions, flags=re.M):
        assert called in defined, f"decision calls undefined effect {called}"


def test_mod_and_harness_agree_on_the_telemetry_schema():
    """A silent version drift here feeds the agent wrong numbers, so it is a test."""
    telemetry = (MOD / "common" / "scripted_effects" / "llm_bridge_telemetry.txt").read_text(
        encoding="utf-8"
    )
    match = re.search(r"LLMB_SCHEMA_VERSION = (\d+)", telemetry)
    assert match, "the mod does not set LLMB_SCHEMA_VERSION"
    assert int(match.group(1)) == SCHEMA_VERSION
