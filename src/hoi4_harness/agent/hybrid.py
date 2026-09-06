"""Hybrid control: the game's AI runs operations, the model runs strategy.

The premise is that HOI4 already ships a competent operational commander. It
moves divisions along a front, plugs holes, reinforces and reacts within a tick,
for free. A language model doing the same job is slower, more expensive, and
worse -- it cannot see the map at the resolution the decision needs, and by the
time it answers the encirclement has closed.

What the model *is* good at is the layer above: who to fight and when, what to
build toward, which doctrine to commit to, when a front is a distraction and
when it is the war. That layer moves on a timescale of weeks, involves tradeoffs
with no local answer, and is exactly what the native AI is worst at.

So the split:

    LLM        who to invade, whom to guarantee, when to enter a war,
               doctrine and research direction, production and construction
               priorities, theater priorities, posture, when to sue for peace

    game AI    front assignment and movement, reinforcement, local reserves,
               tactical retreat and counterattack, garrisoning

The seam is a small directive vocabulary (``set_ai_directive``,
``set_ai_posture``, ``delegate_army_to_ai``) that the LLM Bridge mod turns into
``ai_strategy`` blocks the vanilla AI already understands.

This is groundwork, not a finished system. What is here: the vocabulary, the
control split, the prompt layer, and mock support so it can be exercised. What
is not: the mod-side plumbing that reads a directive's target dynamically, and
any evidence about whether hybrid actually plays better than either half alone.
That last one is the interesting question and it is issue-tracked, not answered.
"""

from __future__ import annotations

#: Actions that only make sense when the model is commanding units directly.
OPERATIONAL_ACTIONS = frozenset({"set_army_order", "set_air_mission", "set_naval_mission"})

#: Actions that only make sense when the native AI is executing.
DIRECTIVE_ACTIONS = frozenset(
    {"delegate_army_to_ai", "set_ai_posture", "set_ai_directive", "clear_ai_directives"}
)

MODES = ("llm", "ai")


def actions_for_mode(allowed: set[str], mode: str) -> set[str]:
    """Narrow the action set to the control mode.

    Offering both vocabularies at once is the failure case: the model gives an
    army a direct order *and* a standing directive, they disagree, and the
    resulting behaviour is attributable to neither.
    """
    if mode == "ai":
        return allowed - OPERATIONAL_ACTIONS
    return allowed - DIRECTIVE_ACTIONS


def describe(mode: str) -> str:
    """One paragraph for the prompt, so the model knows what it is not doing."""
    if mode == "ai":
        return (
            "Operations are delegated. The game's own AI runs fronts, movement, "
            "reinforcement and local reserves; you do not move units. Your job is "
            "intent: who to fight, what to build toward, which theaters matter, "
            "and what posture your forces hold. Express it with set_ai_directive, "
            "set_ai_posture and delegate_army_to_ai."
        )
    return (
        "You command directly. No layer beneath you is making operational "
        "decisions, so front assignment and army orders are yours."
    )
