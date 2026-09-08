"""Read-only state from HOI4 save files.

Status: skeleton. The tokenizer below is real enough to walk a Clausewitz text
save; the mapping from parsed blocks to :class:`GameState` is deliberately left
as a small number of clearly-marked TODOs, because it is the part that has to be
verified against a real save rather than guessed.

Constraints worth knowing before you build on this:

* Ironman saves are binary and compressed. This adapter targets **non-ironman
  text saves** only; that is also the only mode where an autosave cadence you
  control is realistic.
* A save is a snapshot, not a stream. Pair it with an autosave-every-N-days
  setting, or with :mod:`hoi4_harness.adapters.screen` for anything live.
* It is read-only. Compose it with a write-capable sink (see
  :mod:`hoi4_harness.adapters.input_driver`) via :class:`CompositeAdapter`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import find_in_game_dir
from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter

TOKEN = re.compile(r'"[^"]*"|[{}=]|[^\s{}=]+')




def find_save_dir(explicit: Path | None = None) -> Path | None:
    """Locate the save folder, including a Documents redirected into OneDrive."""
    return find_in_game_dir("save games", explicit=explicit)


def latest_save(save_dir: Path) -> Path | None:
    saves = sorted(save_dir.glob("*.hoi4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


def parse_clausewitz(text: str) -> dict:
    """Parse Paradox's key=value / key={...} format into nested dicts.

    Repeated keys become lists. Bare values inside a block are collected under
    the ``"_items"`` key. This handles text saves; binary ironman is out of scope.
    """
    tokens = TOKEN.findall(text)
    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        block: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return block
            pos += 1
            if pos < len(tokens) and tokens[pos] == "=":
                pos += 1
                value_tok = tokens[pos]
                if value_tok == "{":
                    pos += 1
                    value: object = parse_block()
                else:
                    pos += 1
                    value = value_tok.strip('"')
                key = tok.strip('"')
                if key in block:
                    if not isinstance(block[key], list):
                        block[key] = [block[key]]
                    block[key].append(value)
                else:
                    block[key] = value
            else:
                block.setdefault("_items", []).append(tok.strip('"'))
        return block

    return parse_block()


class SaveGameAdapter(GameAdapter):
    """Observes the most recent autosave. Writes nothing."""

    supported_actions = frozenset()

    def __init__(self, save_dir: Path | None = None):
        self.save_dir = find_save_dir(save_dir)
        self._last_path: Path | None = None
        self._last_mtime: float = 0.0

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="savegame",
            readable=True,
            writable=False,
            clock_control=False,
            notes=f"save_dir={self.save_dir or 'not found'}; non-ironman text saves only",
        )

    def read_state(self) -> GameState:
        if not self.save_dir:
            raise FileNotFoundError(
                "No HOI4 save directory found. Set HOI4_SAVE_DIR to the folder holding *.hoi4."
            )
        path = latest_save(self.save_dir)
        if not path:
            raise FileNotFoundError(f"No *.hoi4 saves in {self.save_dir}.")
        self._last_path, self._last_mtime = path, path.stat().st_mtime
        raw = parse_clausewitz(path.read_text(encoding="utf-8", errors="replace"))
        return self._to_state(raw)

    def _to_state(self, raw: dict) -> GameState:
        """Map parsed save blocks onto GameState.

        TODO(#1): confirm the country block key for the player tag against a real
        save; it has moved between patches.
        TODO(#2): production lines, construction queue and division counts.
        TODO(#3): fronts -- derive from combat/army blocks rather than reporting
        the raw province lists, which are far too large for a prompt.

        Until those land, everything not read is reported as unknown rather than
        defaulted to zero.
        """
        state = GameState(raw=raw)
        state.date = str(raw.get("date", state.date)).split(".")[0].replace(".", "-")
        player = raw.get("player")
        if isinstance(player, str):
            state.country = player
        state.unknown_fields = [
            "political_power",
            "stability",
            "war_support",
            "manpower",
            "civilian_factories",
            "military_factories",
            "dockyards",
            "research",
            "production",
            "construction",
            "divisions",
            "fronts",
            "wars",
        ]
        return state

    def apply(self, call: ActionCall) -> ActionResult:
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        """Saves cannot be driven forward. Poll until a newer autosave appears."""
        raise NotImplementedError(
            "SaveGameAdapter is read-only. Wrap it in CompositeAdapter with a "
            "clock-capable sink, or poll wait_for_new_save()."
        )

    def wait_for_new_save(self, timeout_s: float = 300.0, poll_s: float = 5.0) -> GameState:
        """Block until the autosave file changes, then return the new snapshot."""
        import time

        deadline = time.monotonic() + timeout_s
        previous = self._last_mtime
        while time.monotonic() < deadline:
            if self.save_dir:
                path = latest_save(self.save_dir)
                if path and path.stat().st_mtime > previous:
                    return self.read_state()
            time.sleep(poll_s)
        raise TimeoutError(f"No new save within {timeout_s}s.")
