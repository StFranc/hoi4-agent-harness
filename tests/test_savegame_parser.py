"""The Clausewitz save parser.

A hand-written tokenizer with no coverage, sitting under the adapter that is
supposed to read real games. The cases below are the ones that decide whether a
parsed save is trustworthy: repeated keys, nesting, quoted values, and -- the one
that matters most in practice -- a file that was being written while we read it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import SaveGameAdapter, latest_save, parse_clausewitz
from hoi4_harness.types import ActionCall


def test_flat_key_values():
    assert parse_clausewitz("date=1936.1.1\nplayer=SWE") == {"date": "1936.1.1", "player": "SWE"}


def test_nested_blocks():
    parsed = parse_clausewitz("country={ tag=SWE resources={ steel=12 } }")
    assert parsed["country"]["tag"] == "SWE"
    assert parsed["country"]["resources"]["steel"] == "12"


def test_repeated_keys_collapse_into_a_list():
    """Saves repeat keys constantly -- one `division=` per division. Losing all
    but the last is the classic way to read a save wrong."""
    parsed = parse_clausewitz("division={ id=1 } division={ id=2 } division={ id=3 }")
    assert [d["id"] for d in parsed["division"]] == ["1", "2", "3"]


def test_repeated_scalars_collapse_too():
    assert parse_clausewitz("tech=a tech=b")["tech"] == ["a", "b"]


def test_quoted_values_keep_their_spaces_and_lose_their_quotes():
    parsed = parse_clausewitz('name="Kingdom of Sweden" tag="SWE"')
    assert parsed["name"] == "Kingdom of Sweden"
    assert parsed["tag"] == "SWE"


def test_bare_values_inside_a_block_land_in_items():
    parsed = parse_clausewitz("ids={ 4 8 15 16 }")
    assert parsed["ids"]["_items"] == ["4", "8", "15", "16"]


def test_empty_blocks_are_empty_dicts():
    assert parse_clausewitz("focus={ }") == {"focus": {}}


def test_whitespace_and_newlines_do_not_matter():
    dense = parse_clausewitz("a={b=1 c=2}")
    spread = parse_clausewitz("a = {\n\tb = 1\n\tc = 2\n}\n")
    assert dense == spread


def test_a_truncated_file_parses_what_it_can_instead_of_raising():
    """A save being written while we read it is the normal case, not an edge one."""
    parsed = parse_clausewitz("date=1936.1.1 country={ tag=SWE resources={ steel=1")
    assert parsed["date"] == "1936.1.1"
    assert parsed["country"]["tag"] == "SWE"


def test_an_empty_file_is_an_empty_dict():
    assert parse_clausewitz("") == {}


def test_deep_nesting_survives():
    parsed = parse_clausewitz("a={ b={ c={ d={ e=1 } } } }")
    assert parsed["a"]["b"]["c"]["d"]["e"] == "1"


# --- the adapter around it --------------------------------------------------

SAVE = """date=1936.1.1
player="SWE"
country={
\ttag=SWE
\tpolitical_power=25
}
"""


def test_the_adapter_reads_the_newest_save(tmp_path: Path):
    old = tmp_path / "old.hoi4"
    new = tmp_path / "new.hoi4"
    old.write_text(SAVE, encoding="utf-8")
    new.write_text(SAVE.replace("1936.1.1", "1936.6.1"), encoding="utf-8")
    import os

    os.utime(old, (1, 1))
    assert latest_save(tmp_path) == new


def test_reading_a_save_fills_the_date_and_tag_and_admits_the_rest_is_unknown(tmp_path: Path):
    (tmp_path / "autosave.hoi4").write_text(SAVE, encoding="utf-8")
    state = SaveGameAdapter(tmp_path).read_state()
    assert state.country == "SWE"
    assert state.date.startswith("1936")
    # Everything the mapping does not read yet must be declared unknown, never
    # defaulted to a confident zero.
    assert "political_power" in state.unknown_fields
    assert not state.known("production")


def test_a_missing_save_directory_says_what_to_set(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="HOI4_SAVE_DIR"):
        SaveGameAdapter(tmp_path / "nope").read_state()


def test_an_empty_save_directory_is_a_clear_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match=r"No \*\.hoi4 saves"):
        SaveGameAdapter(tmp_path).read_state()


def test_the_adapter_refuses_to_act(tmp_path: Path):
    (tmp_path / "a.hoi4").write_text(SAVE, encoding="utf-8")
    result = SaveGameAdapter(tmp_path).apply(ActionCall("note", {"text": "x"}))
    assert not result.ok and result.error_kind == "unsupported"
