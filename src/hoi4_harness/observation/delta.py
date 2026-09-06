"""Delta briefs: send what changed, not the whole world.

Nothing in a 1936 economy changes minute to minute. Re-sending an identical
situation report every turn is the main way a harness like this burns tokens for
nothing, so the loop sends one full brief and then diffs until either the diff
gets large or `full_brief_every` turns have passed.

Only scalars and queue *shapes* are diffed. Events are always included in full:
they are the reason the turn is happening.
"""

from __future__ import annotations

from ..types import GameState

SCALARS = [
    ("political_power", "PP", 5.0),
    ("stability", "stability", 0.02),
    ("war_support", "war support", 0.02),
    ("manpower", "manpower", 5000),
    ("civilian_factories", "civ factories", 0.5),
    ("military_factories", "mil factories", 0.5),
    ("dockyards", "dockyards", 0.5),
    ("fuel", "fuel", 250),
    ("convoys", "convoys", 5),
]


def _fmt(field: str, value) -> str:
    if field in {"stability", "war_support"}:
        return f"{value * 100:.0f}%"
    if isinstance(value, float):
        return f"{value:.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render_delta(previous: GameState, current: GameState) -> str:
    """A short 'what changed' brief. Returns the date line at minimum."""
    lines = [f"## {current.country} -- {current.date} (changes since {previous.date})"]

    for field, label, threshold in SCALARS:
        if not current.known(field) or not previous.known(field):
            continue
        before, after = getattr(previous, field), getattr(current, field)
        if abs(after - before) >= threshold:
            lines.append(f"{label}: {_fmt(field, before)} -> {_fmt(field, after)}")

    if previous.national_focus != current.national_focus:
        was = previous.national_focus or "none"
        now = current.national_focus or "NONE SELECTED"
        lines.append(f"Focus: {was} -> {now}")

    before_free = sum(1 for s in previous.research if not s.technology)
    after_free = sum(1 for s in current.research if not s.technology)
    if after_free != before_free:
        lines.append(f"Research slots free: {before_free} -> {after_free}")

    if len(previous.construction) != len(current.construction):
        lines.append(f"Construction queue: {len(previous.construction)} -> {len(current.construction)}")

    before_lines = {line.equipment: line.factories for line in previous.production}
    after_lines = {line.equipment: line.factories for line in current.production}
    for equipment in sorted(set(before_lines) | set(after_lines)):
        if before_lines.get(equipment, 0) != after_lines.get(equipment, 0):
            lines.append(
                f"Production {equipment}: {before_lines.get(equipment, 0)} -> "
                f"{after_lines.get(equipment, 0)} factories"
            )

    before_wars = {w.against for w in previous.wars}
    after_wars = {w.against for w in current.wars}
    for tag in sorted(after_wars - before_wars):
        lines.append(f"NEW WAR: {tag}")
    for tag in sorted(before_wars - after_wars):
        lines.append(f"War ended: {tag}")

    for front in current.fronts:
        was = next((f for f in previous.fronts if f.name == front.name), None)
        if was is None or was.pressure != front.pressure:
            lines.append(
                f"Front {front.name}: {front.pressure} "
                f"({front.divisions_friendly}v{front.divisions_enemy})"
            )

    if previous.posture != current.posture:
        lines.append(f"AI posture: {previous.posture or 'default'} -> {current.posture}")
    if set(previous.ai_directives) != set(current.ai_directives):
        added = [d for d in current.ai_directives if d not in previous.ai_directives]
        dropped = [d for d in previous.ai_directives if d not in current.ai_directives]
        if added:
            lines.append("New directives: " + "; ".join(added))
        if dropped:
            lines.append("Dropped directives: " + "; ".join(dropped))
    if len(previous.delegated_armies) != len(current.delegated_armies):
        lines.append(
            f"Armies under AI control: {len(previous.delegated_armies)} -> "
            f"{len(current.delegated_armies)}"
        )

    if current.events:
        lines.append("Since last turn:")
        for event in current.events[:6]:
            marker = {"critical": "!!", "notable": "!"}.get(event.severity, "-")
            lines.append(f"  {marker} {event.text}")

    if len(lines) == 1:
        lines.append("(nothing material changed)")
    return "\n".join(lines)


def delta_is_large(previous: GameState, current: GameState, threshold: int = 12) -> bool:
    """When too much has moved, a full brief is cheaper than an unreadable diff."""
    return len(render_delta(previous, current).splitlines()) > threshold
