A playset small enough to read, for tests/test_identifiers.py.

- `game/` stands in for a vanilla install: four countries, a Swedish focus tree
  with AND and OR prerequisites, a generic default tree, shared focuses, two
  technologies and one state.
- `game/common/on_actions`, `ai_strategy` and `documentation/` exist for
  `tests/test_bridge_check.py`; `on_fixture_focus_done` is invented.
- `mods/new_order/` stands in for a total conversion: it adds a country, and
  its `replace_path` lines drop every vanilla focus tree and state history.

Not game data: ids are invented to exercise the parser, not copied from HOI4.
