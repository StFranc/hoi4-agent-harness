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


LOCALISATION = sorted((MOD / "localisation" / "english").glob("*.yml"))
DECISION_FILES = sorted((MOD / "common" / "decisions").glob("*.txt"))


def _all_localisation() -> str:
    return "\n".join(path.read_text(encoding="utf-8-sig") for path in LOCALISATION)


@pytest.mark.parametrize("path", LOCALISATION, ids=lambda p: p.name)
def test_localisation_is_well_formed(path):
    raw = path.read_bytes()
    # HOI4 only loads localisation saved as UTF-8 *with* a byte-order mark;
    # without one the keys print raw in-game, which no other check would see.
    assert raw.startswith(b"\xef\xbb\xbf"), f"{path.name}: needs a UTF-8 BOM"
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0].strip() == "l_english:"
    assert path.name.endswith("_l_english.yml")
    for line in lines[1:]:
        if line.strip():
            assert re.fullmatch(r' \w+:\d+ ".*"', line), f"bad localisation line: {line!r}"


@pytest.mark.parametrize("path", DECISION_FILES, ids=lambda p: p.name)
def test_every_decision_has_localisation(path):
    decisions = path.read_text(encoding="utf-8")
    loc = _all_localisation()
    names = re.findall(r"^\t(llmb_\w+) = \{", decisions, flags=re.M)
    assert names, f"{path.name}: no decisions found"
    for name in names:
        assert f"{name}:0" in loc, f"decision {name} has no localisation"
        assert f"{name}_desc:0" in loc, f"decision {name} has no description"


@pytest.mark.parametrize("path", DECISION_FILES, ids=lambda p: p.name)
def test_every_decision_category_is_declared(path):
    categories = (MOD / "common" / "decisions" / "categories").glob("*.txt")
    declared = set()
    for category_file in categories:
        declared |= set(re.findall(r"^(\w+) = \{", category_file.read_text(encoding="utf-8"),
                                   flags=re.M))
    used = re.findall(r"^(\w+) = \{", path.read_text(encoding="utf-8"), flags=re.M)
    for name in used:
        assert name in declared, f"{path.name}: category {name} is not declared"
        assert f"{name}:0" in _all_localisation(), f"category {name} has no localisation"


def _defined_effects() -> set[str]:
    defined = set()
    for path in (MOD / "common" / "scripted_effects").glob("*.txt"):
        defined |= set(re.findall(r"^(\w+) = \{", path.read_text(encoding="utf-8"), flags=re.M))
    return defined


@pytest.mark.parametrize("path", DECISION_FILES, ids=lambda p: p.name)
def test_every_effect_the_decisions_call_exists(path):
    defined = _defined_effects()
    for called in re.findall(r"^\t{3}(\w+) = yes", path.read_text(encoding="utf-8"), flags=re.M):
        assert called in defined, f"decision calls undefined effect {called}"


def test_every_effect_an_effect_calls_exists():
    defined = _defined_effects()
    for path in (MOD / "common" / "scripted_effects").glob("*.txt"):
        body = strip_comments(path.read_text(encoding="utf-8"))
        for called in re.findall(r"\b(llm_bridge_\w+) = yes", body):
            assert called in defined, f"{path.name} calls undefined effect {called}"


def test_the_generated_directive_files_are_current():
    """The per-target files come from modgen.py; hand edits or a stale
    regeneration would let the harness and the mod disagree about targets."""
    from hoi4_harness import modgen

    stale = modgen.drift(MOD)
    assert not stale, f"regenerate with `hoi4-harness mod-directives`: {stale}"


def test_mod_and_harness_agree_on_the_telemetry_schema():
    """A silent version drift here feeds the agent wrong numbers, so it is a test."""
    telemetry = (MOD / "common" / "scripted_effects" / "llm_bridge_telemetry.txt").read_text(
        encoding="utf-8"
    )
    match = re.search(r"LLMB_SCHEMA_VERSION = (\d+)", telemetry)
    assert match, "the mod does not set LLMB_SCHEMA_VERSION"
    assert int(match.group(1)) == SCHEMA_VERSION
