"""Front summaries: the part of a wartime brief that does not compress by itself.

A peacetime brief is a dozen lines whatever happens. A war adds one line per
front, and a real war has many -- so front rendering is the one place where the
cost story of this harness can quietly fall apart in 1939. The rule here keeps
it flat: however many fronts exist, the brief shows at most ``limit`` of them in
full, ordered by how much they need a decision, and folds the rest into one
line that still says whether anything in the fold is in trouble.

Salience is a sort key, not a model: a front that is losing ground outranks a
pocket forming, which outranks a threatened capital, which outranks a supply
problem, which outranks an attack. Ties go to the bigger engagement. Anything
with none of those is *quiet*, and quiet fronts are what the fold is for.
"""

from __future__ import annotations

from ..types import Front

#: Below this fraction of supply demand met, a sector counts as poorly supplied.
LOW_SUPPLY = 0.5


def is_quiet(front: Front) -> bool:
    """Nothing about this front asks for a decision this week."""
    return (
        front.pressure == "stable"
        and front.stance == "hold"
        and not front.pocket_divisions
        and not front.threatens_capital
        and not (front.supply is not None and front.supply < LOW_SUPPLY)
    )


def salience(front: Front) -> tuple:
    """Sort key, most urgent first when sorted ascending."""
    return (
        front.pressure != "losing_ground",
        not front.pocket_divisions,
        not front.threatens_capital,
        not (front.supply is not None and front.supply < LOW_SUPPLY),
        front.stance != "offensive" and front.pressure != "advancing",
        -(front.divisions_friendly + front.divisions_enemy),
        front.name,
    )


def front_line(front: Front) -> str:
    """One line for one front, flags in capitals so they survive skimming."""
    bits = [f"{front.divisions_friendly}v{front.divisions_enemy}", front.stance]
    bits.append("LOSING GROUND" if front.pressure == "losing_ground" else front.pressure)
    if front.supply is not None:
        supply = f"supply {front.supply * 100:.0f}%"
        bits.append(supply.upper() if front.supply < LOW_SUPPLY else supply)
    if front.pocket_divisions:
        bits.append(f"POCKET FORMING ({front.pocket_divisions} divs at risk)")
    if front.threatens_capital:
        bits.append("CAPITAL THREATENED")
    return f"Front {front.name} vs {front.enemy}: " + ", ".join(bits)


def summarise_fronts(fronts: list[Front], limit: int = 3) -> list[str]:
    """At most ``limit`` front lines plus one folded line, whatever the war.

    A fold never hides trouble silently: if any folded front is not quiet, the
    fold line says how many, so the model knows to ask rather than assume.
    """
    if not fronts:
        return []
    ranked = sorted(fronts, key=salience)
    # A quiet front only earns a full line when there is room; the fold is
    # cheaper and says the same thing about it.
    shown = [f for f in ranked if not is_quiet(f)][:limit]
    if len(shown) < limit:
        shown += [f for f in ranked if is_quiet(f)][: limit - len(shown)]
    shown.sort(key=salience)
    folded = [f for f in ranked if f not in shown]

    lines = [front_line(front) for front in shown]
    if folded:
        friendly = sum(f.divisions_friendly for f in folded)
        enemy = sum(f.divisions_enemy for f in folded)
        restless = sum(1 for f in folded if not is_quiet(f))
        noun = "sector" if len(folded) == 1 else "sectors"
        if restless:
            lines.append(
                f"+ {len(folded)} more {noun} ({friendly}v{enemy}), "
                f"{restless} of them NOT QUIET"
            )
        else:
            lines.append(f"+ {len(folded)} quiet {noun} ({friendly}v{enemy})")
    return lines


def front_changes(previous: list[Front], current: list[Front], limit: int = 4) -> list[str]:
    """What moved on the fronts since the last brief, capped like the brief is.

    Pressure changes, supply collapsing or recovering, a pocket opening or
    closing, the capital coming under threat, and fronts that opened or closed.
    """
    before = {f.name: f for f in previous}
    changes: list[tuple[tuple, str]] = []
    for front in current:
        was = before.get(front.name)
        if was is None:
            changes.append((salience(front), "New f" + front_line(front)[1:]))
            continue
        what: list[str] = []
        if was.pressure != front.pressure:
            what.append(f"{was.pressure} -> {front.pressure}")
        if _supply_band(was) != _supply_band(front) and front.supply is not None:
            before_text = "unknown" if was.supply is None else f"{was.supply * 100:.0f}%"
            what.append(f"supply {before_text} -> {front.supply * 100:.0f}%")
        if bool(was.pocket_divisions) != bool(front.pocket_divisions):
            what.append(
                f"POCKET FORMING ({front.pocket_divisions} divs)" if front.pocket_divisions
                else "pocket closed"
            )
        if was.threatens_capital != front.threatens_capital:
            what.append("CAPITAL THREATENED" if front.threatens_capital else "capital safe")
        if what:
            changes.append((
                salience(front),
                f"Front {front.name} ({front.divisions_friendly}v{front.divisions_enemy}): "
                + ", ".join(what),
            ))
    names = {f.name for f in current}
    for gone in sorted(set(before) - names):
        changes.append(((True,) * 5 + (0, gone), f"Front {gone}: closed"))

    changes.sort(key=lambda pair: pair[0])
    lines = [text for _, text in changes[:limit]]
    if len(changes) > limit:
        lines.append(f"+ {len(changes) - limit} more front change(s)")
    return lines


def _supply_band(front: Front) -> str:
    if front.supply is None:
        return "unknown"
    return "low" if front.supply < LOW_SUPPLY else "ok"
