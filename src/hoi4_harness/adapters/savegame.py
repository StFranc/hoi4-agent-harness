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


#: A key at the very start of a line: how a text save lays out its top level.
TOP_LEVEL = re.compile(r"^([A-Za-z0-9_\-]+)\s*=", re.M)


def top_level_spans(text: str) -> dict[str, list[tuple[int, int]]]:
    """Where each top-level entry of a save starts and ends, without parsing it.

    Saves on the machine this was checked on run 68-96 MB, and tokenizing the
    whole file to read a handful of numbers is most of the cost. A text save
    writes its top level at column 0 and everything nested indented, so a key
    at the start of a line opens an entry that runs to the next one. That is a
    layout assumption, not a grammar: when it finds nothing (a file on one
    line), callers fall back to a full parse.
    """
    starts = [(m.start(), m.group(1)) for m in TOP_LEVEL.finditer(text)]
    spans: dict[str, list[tuple[int, int]]] = {}
    for index, (start, key) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        spans.setdefault(key, []).append((start, end))
    return spans


def extract(text: str, keys: set[str]) -> dict:
    """Parse only the top-level entries named in ``keys``.

    Equivalent to ``{k: parse_clausewitz(text)[k] for k in keys}`` for a
    normally laid-out save, at the cost of the entries asked for rather than
    the whole file.
    """
    spans = top_level_spans(text)
    if not spans or not keys <= set(spans):
        # The layout did not show every key (a save on one line, or a key the
        # heuristic missed): parse it all rather than report a key absent.
        full = parse_clausewitz(text)
        return {k: v for k, v in full.items() if k in keys}
    parsed: dict = {}
    for key in keys:
        for start, end in spans.get(key, []):
            for k, v in parse_clausewitz(text[start:end]).items():
                if k in parsed:
                    if not isinstance(parsed[k], list):
                        parsed[k] = [parsed[k]]
                    parsed[k].append(v)
                else:
                    parsed[k] = v
    return parsed


def country_block(text: str, tag: str) -> dict | None:
    """The parsed block for one country, or None if this save does not lay it
    out as ``countries={ TAG={ ... } }``.

    The container name is the part most likely to have moved between patches,
    which is why ``hoi4-harness save-inspect`` exists: it shows what a real save
    calls it instead of this guessing harder.
    """
    spans = top_level_spans(text).get("countries")
    if not spans:
        return None
    start, end = spans[0]
    body = text[start:end]
    match = re.search(rf"^\s{{1,2}}{re.escape(tag)}\s*=\s*\{{", body, flags=re.M)
    if not match:
        return None
    # The country's block runs to the next key at the same indentation.
    indent = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
    following = re.compile(rf"^{re.escape(indent)}[A-Za-z0-9_\-]+\s*=", re.M)
    nxt = following.search(body, match.end())
    chunk = body[match.start(): nxt.start() if nxt else len(body)]
    return parse_clausewitz(chunk).get(tag)


def save_date(raw: str) -> str | None:
    """``1936.1.1.12`` -> ``1936-01-01``. The hour is noise here."""
    from .logtail import parse_date

    return parse_date(str(raw).strip('"'))


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
        text = path.read_text(encoding="utf-8", errors="replace")
        # Only what the mapping reads, never the whole 90 MB tokenized.
        raw = extract(text, {"date", "player"})
        player = raw.get("player")
        if isinstance(player, str):
            raw["country"] = country_block(text, player)
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
        # This used to keep only the year: '1936.1.1.12'.split('.')[0].
        state.date = save_date(raw.get("date", "")) or state.date
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
            "completed_focuses",
            # Not read from the save either. Reported unknown so nothing -- the
            # wake rules, the input driver's verification -- mistakes an empty
            # default for "no focus running" or "no directive standing".
            "national_focus",
            "delegated_armies",
            "ai_directives",
            "posture",
            "theater_postures",
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


def inspect(path: Path, limit: int = 60) -> str:
    """What a real save actually contains, for finishing the mapping (#6).

    The mapping from save blocks to GameState has to be checked against a real
    save, not written from a wiki: block names move between patches. This
    prints what there is to map -- the top-level entries by size, and the
    player country's own keys with a sample of each value -- so that work
    starts from the file rather than from memory.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    spans = top_level_spans(text)
    head = extract(text, {"date", "player"})
    lines = [
        f"{path}  ({len(text) / 1_000_000:.1f} MB)",
        f"date     {head.get('date', '?')}  -> {save_date(head.get('date', '')) or 'unparsed'}",
        f"player   {head.get('player', '?')}",
    ]
    if not spans:
        lines.append("no top-level layout found (one-line or binary save?); nothing more to show")
        return "\n".join(lines)
    sizes = sorted(
        ((key, sum(end - start for start, end in ranges), len(ranges)) for key, ranges in spans.items()),
        key=lambda item: -item[1],
    )
    lines.append("")
    lines.append(f"top-level entries ({len(sizes)}), largest first:")
    for key, size, count in sizes[:25]:
        lines.append(f"  {key:<32} {size / 1000:>10,.0f} kB" + (f"  x{count}" if count > 1 else ""))
    player = head.get("player")
    if not isinstance(player, str):
        return "\n".join(lines)
    block = country_block(text, player)
    lines.append("")
    if block is None:
        lines.append(f"no countries={{ {player}={{...}} }} block: the player country lives somewhere "
                     "else in this version. Look for it among the entries above.")
        return "\n".join(lines)
    lines.append(f"{player}'s block: {len(block)} keys")
    for key, value in list(block.items())[:limit]:
        lines.append(f"  {key:<32} {_sample(value)}")
    if len(block) > limit:
        lines.append(f"  ... and {len(block) - limit} more")
    return "\n".join(lines)


def _sample(value) -> str:
    if isinstance(value, dict):
        keys = [k for k in value if k != "_items"]
        return "{ " + " ".join(keys[:6]) + (" ..." if len(keys) > 6 else "") + " }"
    if isinstance(value, list):
        return f"[{len(value)} entries] first: {_sample(value[0])}"
    return str(value)[:60]
