"""Render a GameState as a compact brief.

The brief is the single biggest recurring cost in a run, so it is written to a
budget: roughly 250-500 tokens for a full one, and a fraction of that for a
delta. Rules applied here:

* numbers, not prose -- no sentences the model can infer from a table;
* omit empty sections entirely rather than printing "none";
* collapse long lists (divisions, stockpiles) to the few that matter;
* say "unknown" for anything the adapter could not read, never 0.
"""

from __future__ import annotations

from ..types import GameState

_SEV = {"critical": "!!", "notable": "!", "info": "-"}


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _val(state: GameState, field: str, fmt=str) -> str:
    return "unknown" if not state.known(field) else fmt(getattr(state, field))


def render_full(state: GameState, max_events: int = 6) -> str:
    """A complete situation brief."""
    lines: list[str] = []
    lines.append(f"## {state.country_name} ({state.country}) -- {state.date}")

    lines.append(
        "PP {pp} | stability {stab} | war support {ws} | manpower {mp}".format(
            pp=_val(state, "political_power", lambda v: f"{v:.0f}"),
            stab=_val(state, "stability", _pct),
            ws=_val(state, "war_support", _pct),
            mp=_val(state, "manpower", lambda v: f"{v:,}"),
        )
    )
    lines.append(
        "Factories: {civ} civ / {mil} mil / {dock} dockyards | fuel {fuel} | convoys {conv}".format(
            civ=_val(state, "civilian_factories"),
            mil=_val(state, "military_factories"),
            dock=_val(state, "dockyards"),
            fuel=_val(state, "fuel", lambda v: f"{v:.0f}"),
            conv=_val(state, "convoys"),
        )
    )

    if state.resources:
        detail = ", ".join(f"{k} {v}" for k, v in sorted(state.resources.items()))
        lines.append(f"Resources: {detail}")

    if state.national_focus:
        lines.append(f"Focus: {state.national_focus} ({state.focus_days_remaining}d left)")
    else:
        lines.append("Focus: NONE SELECTED")

    busy = [s for s in state.research if s.technology]
    free = len(state.research) - len(busy)
    if state.known("research"):
        detail = "; ".join(f"{s.technology} ({s.days_remaining}d)" for s in busy) or "idle"
        lines.append(f"Research: {detail}" + (f" | {free} SLOT(S) FREE" if free else ""))

    if state.production:
        assigned = sum(line.factories for line in state.production)
        detail = "; ".join(
            f"{line.equipment} x{line.factories} ({_pct(line.efficiency)} eff)"
            for line in state.production[:6]
        )
        idle = (state.military_factories - assigned) if state.known("military_factories") else 0
        lines.append(f"Production: {detail}" + (f" | {idle} MIL FACTORIES IDLE" if idle > 0 else ""))

    if state.construction:
        head = state.construction[0]
        lines.append(
            f"Construction: {len(state.construction)} queued, "
            f"next {head.building} in {head.state} ({_pct(head.progress)})"
        )
    elif state.known("construction"):
        lines.append("Construction: QUEUE EMPTY")

    if state.divisions:
        total = sum(g.count for g in state.divisions)
        by_template = "; ".join(f"{g.count}x {g.template} @ {g.location}" for g in state.divisions[:5])
        lines.append(f"Army: {total} divisions -- {by_template}")

    if state.stockpiles:
        top = sorted(state.stockpiles.items(), key=lambda kv: -kv[1])[:4]
        lines.append("Stockpile: " + ", ".join(f"{k} {v:,}" for k, v in top))

    if state.wars:
        for war in state.wars:
            lines.append(f"AT WAR with {war.against} since {war.since} (war score {war.war_score:+.0f})")
    if state.fronts:
        for front in state.fronts:
            lines.append(
                f"Front {front.name} vs {front.enemy}: {front.divisions_friendly}v"
                f"{front.divisions_enemy}, {front.stance}, {front.pressure}"
            )

    if state.delegated_armies or state.ai_directives or state.posture:
        bits = []
        if state.delegated_armies:
            bits.append(f"{len(state.delegated_armies)} army(s) delegated to the game AI")
        if state.posture:
            bits.append(f"posture {state.posture}")
        if state.ai_directives:
            bits.append("directives: " + "; ".join(state.ai_directives[:4]))
        lines.append("AI control: " + " | ".join(bits))

    if state.events:
        lines.append("Since last turn:")
        for event in state.events[:max_events]:
            lines.append(f"  {_SEV.get(event.severity, '-')} {event.text}")
        if len(state.events) > max_events:
            lines.append(f"  ... and {len(state.events) - max_events} more")

    if state.unknown_fields:
        lines.append(f"(not observable with this adapter: {', '.join(state.unknown_fields[:8])})")

    return "\n".join(lines)
