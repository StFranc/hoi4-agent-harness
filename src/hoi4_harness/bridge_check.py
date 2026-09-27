"""Check the LLM Bridge mod against a real install and a real log (#1, #2).

Every script token in the mod was written from memory, and Paradox script fails
silently: an unknown ``on_action`` never fires, an unknown ``ai_strategy`` type
is ignored, a variable that does not expand prints literally. Only the game can
say which tokens are real -- but most of the checking does not need the game
*running*, only its files and one log from a session with the mod enabled.

Two checks, each a report rather than a verdict:

* **tokens** -- against the install: every ``on_action`` the mod hooks must be
  defined somewhere in ``common/on_actions``; every ``ai_strategy`` type the mod
  raises should appear in the game's own ``common/ai_strategy`` files; every
  trigger the telemetry reads should appear in the install's documentation.
  A token the install never uses is *suspect*, not proven wrong -- the report
  says which. It also lists the install's own on_actions whose names look like
  focus, research or construction completion: the candidates #2 needs.
* **log** -- against a ``game.log``: which telemetry fields expanded to real
  values and which printed a literal token, whether the engine's date prefix is
  there, which event codes arrived. That is the acceptance line of #1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.logtail import EVENT_CODES, FIELDS, PREFIX, engine_date, parse_date, parse_line

MOD_DIR = Path(__file__).resolve().parents[2] / "mod" / "llm_bridge"

#: Trigger tokens the telemetry reads, as written in llm_bridge_telemetry.txt.
TELEMETRY_TRIGGERS = ("political_power", "stability", "war_support", "num_of_civilian_factories",
                      "num_of_military_factories", "num_of_naval_factories", "manpower",
                      "num_divisions")

#: Words that make an on_action a candidate for #2's completion events.
COMPLETION_HINTS = ("focus", "research", "tech", "construct", "building")


def _strip(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _on_action_names(text: str) -> set[str]:
    """Names defined inside ``on_actions = { ... }`` blocks, at their first level."""
    names: set[str] = set()
    body = _strip(text)
    for match in re.finditer(r"\bon_actions\s*=\s*\{", body):
        depth, i = 1, match.end()
        token = re.compile(r"(\w+)\s*=\s*\{|\{|\}")
        while depth and i < len(body):
            m = token.search(body, i)
            if not m:
                break
            if m.group(0) == "}":
                depth -= 1
            else:
                if depth == 1 and m.group(1):
                    names.add(m.group(1))
                depth += 1
            i = m.end()
    return names


def _strategy_types(text: str) -> set[str]:
    return set(re.findall(r"\btype\s*=\s*(\w+)", _strip(text)))


@dataclass
class TokenReport:
    on_actions_missing: list[str] = field(default_factory=list)
    on_actions_found: list[str] = field(default_factory=list)
    strategy_types_unseen: list[str] = field(default_factory=list)
    strategy_types_seen: list[str] = field(default_factory=list)
    triggers_unseen: list[str] = field(default_factory=list)
    documentation_found: bool = False
    completion_candidates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.on_actions_missing

    def render(self) -> str:
        lines = ["tokens, against the install:"]
        lines.append(f"  on_actions defined:   {', '.join(self.on_actions_found) or '-'}")
        if self.on_actions_missing:
            lines.append(f"  on_actions MISSING:   {', '.join(self.on_actions_missing)}"
                         "  <- these never fire")
        lines.append(f"  ai_strategy types the game uses too: {', '.join(self.strategy_types_seen) or '-'}")
        if self.strategy_types_unseen:
            lines.append(f"  ai_strategy types the game never uses: {', '.join(self.strategy_types_unseen)}"
                         "  <- suspect: an unknown type is ignored silently")
        if not self.documentation_found:
            lines.append("  triggers: no documentation/ folder in the install; not checked")
        elif self.triggers_unseen:
            lines.append(f"  triggers not in the install's documentation: {', '.join(self.triggers_unseen)}"
                         "  <- suspect")
        else:
            lines.append("  triggers: every one the telemetry reads is documented")
        lines.append("  completion on_actions for #2 (focus/research/construction candidates): "
                     + (", ".join(self.completion_candidates) or "none found"))
        return "\n".join(lines)


def check_tokens(game_dir: Path, mod_dir: Path = MOD_DIR) -> TokenReport:
    report = TokenReport()
    game_dir, mod_dir = Path(game_dir), Path(mod_dir)

    defined: set[str] = set()
    for path in sorted((game_dir / "common" / "on_actions").glob("*.txt")):
        defined |= _on_action_names(path.read_text(encoding="utf-8-sig", errors="replace"))
    used: set[str] = set()
    for path in sorted((mod_dir / "common" / "on_actions").glob("*.txt")):
        used |= _on_action_names(path.read_text(encoding="utf-8", errors="replace"))
    report.on_actions_found = sorted(used & defined)
    report.on_actions_missing = sorted(used - defined)
    report.completion_candidates = sorted(
        name for name in defined if any(hint in name for hint in COMPLETION_HINTS)
    )

    seen: set[str] = set()
    for folder in ("ai_strategy", "ai_strategy_plans"):
        for path in sorted((game_dir / "common" / folder).glob("*.txt")):
            seen |= _strategy_types(path.read_text(encoding="utf-8-sig", errors="replace"))
    mod_types: set[str] = set()
    for path in sorted((mod_dir / "common" / "ai_strategy").glob("*.txt")):
        mod_types |= _strategy_types(path.read_text(encoding="utf-8", errors="replace"))
    report.strategy_types_seen = sorted(mod_types & seen)
    report.strategy_types_unseen = sorted(mod_types - seen)

    docs = game_dir / "documentation"
    if docs.is_dir():
        report.documentation_found = True
        corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                           for p in sorted(docs.rglob("*")) if p.is_file())
        report.triggers_unseen = [t for t in TELEMETRY_TRIGGERS
                                  if not re.search(rf"\b{re.escape(t)}\b", corpus)]
    return report


@dataclass
class LogReport:
    lines: int = 0
    expanded: dict[str, str] = field(default_factory=dict)
    literal: dict[str, str] = field(default_factory=dict)
    engine_dated: int = 0
    mod_date_shapes: set[str] = field(default_factory=set)
    events: dict[str, int] = field(default_factory=dict)
    schema_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.lines > 0 and not self.literal and self.schema_error is None

    def render(self) -> str:
        if self.schema_error:
            return f"log: {self.schema_error}"
        if not self.lines:
            return ("log: no LLMB telemetry lines. Is the mod enabled? The 'Emit telemetry now' "
                    "decision forces one.")
        lines = [f"log: {self.lines} telemetry line(s)"]
        lines.append("  expanded:  " + (", ".join(f"{k}={v}" for k, v in sorted(self.expanded.items()))
                                        or "none"))
        if self.literal:
            lines.append("  LITERAL:   " + ", ".join(f"{k}={v}" for k, v in sorted(self.literal.items()))
                         + "  <- the token did not expand; the field reads as unknown")
        lines.append(f"  engine date prefix on {self.engine_dated}/{self.lines} line(s)"
                     + ("" if self.engine_dated else "  <- dates come from the mod's field alone"))
        if self.mod_date_shapes:
            lines.append("  mod date field shapes: " + ", ".join(sorted(self.mod_date_shapes)))
        if self.events:
            named = [f"{EVENT_CODES.get(code, (f'code {code}',))[0]} x{n}"
                     for code, n in sorted(self.events.items())]
            lines.append("  events: " + ", ".join(named))
        return "\n".join(lines)


def check_log(log_path: Path) -> LogReport:
    report = LogReport()
    for raw in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if PREFIX not in raw:
            continue
        try:
            parsed = parse_line(raw)
        except ValueError as exc:
            report.schema_error = str(exc)
            return report
        if not parsed:
            continue
        kind, fields = parsed
        report.lines += 1
        if engine_date(raw):
            report.engine_dated += 1
        if kind in {"state", "boot"}:
            date_field = fields.get("date") or fields.get("start")
            if date_field:
                report.mod_date_shapes.add("parsed" if parse_date(date_field) else f"unparsed: {date_field}")
            for key, (_, convert) in FIELDS.items():
                if key not in fields:
                    continue
                try:
                    convert(fields[key])
                except ValueError:
                    report.literal[key] = fields[key]
                else:
                    report.expanded[key] = fields[key]
        elif kind == "evt":
            code = fields.get("kind", "?")
            report.events[code] = report.events.get(code, 0) + 1
    # A field that expanded on any line is real; a literal elsewhere was an
    # early tick, not a broken token.
    for key in list(report.literal):
        if key in report.expanded:
            del report.literal[key]
    return report
