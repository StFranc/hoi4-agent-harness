"""Read state from the LLM Bridge mod's telemetry in ``game.log``.

This is the best read path the harness has. A save file is exact but stale and
expensive to parse; a screenshot is live but needs a vision call. The mod makes
the game itself print a structured line every tick, and tailing a log file is
free and immediate.

Pair it with a writer (``--adapter logtail+input``). It observes; it does not act.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..paths import find_in_game_dir
from ..types import ActionCall, ActionResult, GameEvent, GameState
from .base import AdapterInfo, GameAdapter
from .clock import wait_for_days

#: Must match LLMB_SCHEMA_VERSION in the mod's telemetry effect.
SCHEMA_VERSION = 1

PREFIX = "LLMB|"
LINE = re.compile(r"LLMB\|v(?P<version>\d+)\|(?P<kind>\w+)\|(?P<fields>.*)$")
#: The engine stamps every line with the in-game date: [hh:mm:ss][Y.M.D.H][file:line].
ENGINE_DATE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\[(?P<date>\d{4}\.\d{1,2}\.\d{1,2})\.\d{1,2}\]")



# Numeric event codes the mod emits, mapped to harness event kinds and severity.
EVENT_CODES = {
    "1": ("war_declared", "critical"),
    "2": ("capitulation", "critical"),
    "3": ("focus_complete", "notable"),
    "4": ("research_done", "notable"),
    "10": ("posture_defensive", "info"),
    "11": ("posture_offensive", "info"),
    "12": ("directives_cleared", "info"),
}

# Telemetry field -> (GameState attribute, converter)
FIELDS = {
    "pp": ("political_power", float),
    "stab": ("stability", lambda v: float(v) / 100.0),
    "ws": ("war_support", lambda v: float(v) / 100.0),
    "civ": ("civilian_factories", int),
    "mil": ("military_factories", int),
    "doc": ("dockyards", int),
    "mp": ("manpower", int),
}

# Fields the mod does not emit yet. Reported as unknown rather than defaulted.
NOT_EMITTED = ["research", "production", "construction", "fronts", "wars", "national_focus"]


def find_log(explicit: Path | None = None) -> Path | None:
    """Locate game.log, including a Documents folder redirected into OneDrive."""
    return find_in_game_dir("logs", "game.log", explicit=explicit)


def parse_date(raw: str) -> str | None:
    """Accept the shapes the game's date token can produce.

    Which one you get depends on the token used in the mod and on locale, so the
    parser is permissive and returns ``None`` rather than guessing.
    """
    raw = raw.strip()
    # The game writes Y.M.D and, in log prefixes, Y.M.D.H -- the hour is noise here.
    match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.\d{1,2})?", raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return date(year, month, day).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    match = re.fullmatch(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if match:
        months = ["january", "february", "march", "april", "may", "june", "july",
                  "august", "september", "october", "november", "december"]
        name = match.group(2).lower()
        if name in months:
            return date(int(match.group(3)), months.index(name) + 1, int(match.group(1))).isoformat()
    return None


def engine_date(line: str) -> str | None:
    """The in-game date from a log line's own prefix, if it has one.

    Worth preferring over anything the mod prints: the engine stamps it on every
    line, so it cannot be broken by a script token that did not expand.
    """
    match = ENGINE_DATE.match(line.strip())
    return parse_date(match.group("date")) if match else None


def parse_line(line: str) -> tuple[str, dict[str, str]] | None:
    """One ``LLMB|`` line -> (kind, fields). ``None`` if it is not ours."""
    match = LINE.search(line)
    if not match:
        return None
    if int(match.group("version")) != SCHEMA_VERSION:
        raise ValueError(
            f"LLM Bridge schema v{match.group('version')} but this harness speaks "
            f"v{SCHEMA_VERSION}. Update the mod or the harness -- do not run mismatched."
        )
    fields: dict[str, str] = {}
    for chunk in match.group("fields").split("|"):
        key, _, value = chunk.partition("=")
        if _:
            fields[key.strip()] = value.strip()
    return match.group("kind"), fields


class LogTailAdapter(GameAdapter):
    """Follows game.log and rebuilds GameState from the mod's telemetry."""

    supported_actions = frozenset()

    def __init__(
        self,
        log_path: Path | None = None,
        country: str | None = None,
        poll_seconds: float = 0.5,
    ):
        self.log_path = find_log(log_path)
        self.poll_seconds = poll_seconds
        self.country = country
        self._offset = 0
        self._state = GameState(unknown_fields=list(NOT_EMITTED))
        self._pending: list[GameEvent] = []
        self._seen_any = False

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="logtail",
            readable=True,
            writable=False,
            clock_control=False,
            notes=(
                f"log={self.log_path or 'not found'}; requires the LLM Bridge mod "
                f"(schema v{SCHEMA_VERSION})"
            ),
        )

    # --- reading -------------------------------------------------------------

    def poll(self) -> int:
        """Consume whatever has been appended since the last poll.

        Returns the number of telemetry lines applied. A truncated or rotated log
        (size shrank) resets the offset rather than reading garbage.
        """
        if not self.log_path or not self.log_path.exists():
            raise FileNotFoundError(
                "No game.log found. Set HOI4_LOG_PATH, and check the LLM Bridge mod is enabled."
            )
        size = self.log_path.stat().st_size
        if size < self._offset:
            self._offset = 0
        applied = 0
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self._offset)
            for line in handle:
                if PREFIX not in line:
                    continue
                parsed = parse_line(line)
                if parsed:
                    kind, fields = parsed
                    stamped = engine_date(line)
                    if stamped:
                        fields.setdefault("_engine_date", stamped)
                    self._apply(kind, fields)
                    applied += 1
            self._offset = handle.tell()
        return applied

    def _apply(self, kind: str, fields: dict[str, str]) -> None:
        state = self._state
        if self.country and fields.get("tag") not in (None, self.country):
            return

        if kind in {"state", "boot"}:
            self._seen_any = True
            if "tag" in fields:
                state.country = fields["tag"]
            iso = fields.get("_engine_date") or parse_date(
                fields.get("date", "") or fields.get("start", "")
            )
            if iso:
                state.date = iso
            for key, (attribute, convert) in FIELDS.items():
                if key in fields:
                    try:
                        setattr(state, attribute, convert(fields[key]))
                    except ValueError:
                        # A token the game did not expand prints literally. Treat
                        # it as missing, never as zero.
                        if attribute not in state.unknown_fields:
                            state.unknown_fields.append(attribute)
                        continue
                    if attribute in state.unknown_fields:
                        state.unknown_fields.remove(attribute)
        elif kind == "evt":
            code = fields.get("kind", "")
            event_kind, severity = EVENT_CODES.get(code, (code or "unknown", "info"))
            self._pending.append(
                GameEvent(
                    kind=event_kind,
                    text=fields.get("detail") or event_kind.replace("_", " "),
                    severity=severity,
                    date=state.date,
                )
            )

    def read_state(self) -> GameState:
        self.poll()
        if not self._seen_any:
            raise RuntimeError(
                "game.log has no LLM Bridge telemetry. Enable the mod, or use "
                "the 'Emit telemetry now' decision to force a line."
            )
        self._state.events = list(self._pending)
        self._pending = []
        return self._state

    def apply(self, call: ActionCall) -> ActionResult:
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        """Watch the clock until the date has moved, or something critical lands.

        This adapter never touches the clock -- it has no write channel and, in
        co-op, no business taking it. The game runs at whatever speed the player
        or the writer set.
        """
        return wait_for_days(self.read_state, days, self.poll_seconds)
