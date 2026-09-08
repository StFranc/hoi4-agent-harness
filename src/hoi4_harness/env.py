"""The environment: adapter + validation + observation, behind one object.

Everything the agent can do to the world goes through here, which makes this the
one place to enforce the rules that must not depend on the model behaving:
validation, the confirmation gate on irreversible actions, and the per-turn
action cap.
"""

from __future__ import annotations

from .actions import registry
from .adapters.base import GameAdapter
from .config import HarnessConfig
from .observation import ObservationBuilder
from .types import ActionCall, ActionResult, GameState, Observation


class HOI4Env:
    def __init__(self, adapter: GameAdapter, config: HarnessConfig | None = None):
        self.adapter = adapter
        self.config = config or HarnessConfig()
        self.builder = ObservationBuilder(full_brief_every=self.config.full_brief_every)
        self.turn = 0
        self.confirm_hook = None  # set to a callable(ActionCall) -> bool for human approval

    # --- observation ---------------------------------------------------------

    @property
    def allowed_actions(self) -> set[str]:
        """Adapter capability, narrowed by the operator's whitelist/blacklist."""
        return self.config.allowed_actions(set(self.adapter.supported_actions))

    def read_state(self) -> GameState:
        return self.adapter.read_state()

    def observe(self, notes: list[str] | None = None) -> Observation:
        state = self.adapter.read_state()
        return self.builder.build(
            state,
            turn=self.turn,
            legal_actions=sorted(self.allowed_actions),
            notes=notes,
        )

    @property
    def owns_clock(self) -> bool:
        return self.config.clock_owner == "harness"

    def reset(self) -> Observation:
        self.turn = 0
        if self.owns_clock:
            self.adapter.pause()
        return self.observe()

    # --- acting --------------------------------------------------------------

    def act(self, call: ActionCall) -> ActionResult:
        """Validate, gate, then apply one action."""
        problem = registry.check(call, self.allowed_actions)
        if problem is not None:
            return problem

        if registry.needs_confirmation(call) and self.config.require_confirmation:
            if self.config.dry_run:
                return ActionResult(
                    ok=False,
                    action=call.name,
                    call_id=call.call_id,
                    message=(
                        f"'{call.name}' is irreversible and the harness is in dry-run mode. "
                        "Nothing was done. Re-run with --live to allow it."
                    ),
                    error_kind="rejected",
                )
            if self.confirm_hook and not self.confirm_hook(call):
                return ActionResult(
                    ok=False,
                    action=call.name,
                    call_id=call.call_id,
                    message="A human declined this action.",
                    error_kind="rejected",
                )

        try:
            return self.adapter.apply(call)
        except Exception as exc:  # noqa: BLE001 - an adapter fault must not end the run
            return ActionResult(
                ok=False,
                action=call.name,
                call_id=call.call_id,
                message=f"Adapter raised {type(exc).__name__}: {exc}",
                error_kind="rejected",
            )

    def act_many(self, calls: list[ActionCall]) -> list[ActionResult]:
        results: list[ActionResult] = []
        for call in calls[: self.config.max_actions_per_turn]:
            results.append(self.act(call))
        if len(calls) > self.config.max_actions_per_turn:
            results.append(
                ActionResult(
                    ok=False,
                    action="(dropped)",
                    message=(
                        f"Only the first {self.config.max_actions_per_turn} actions ran; "
                        f"{len(calls) - self.config.max_actions_per_turn} were dropped."
                    ),
                    error_kind="budget",
                )
            )
        return results

    # --- clock ---------------------------------------------------------------

    def advance(self, days: int | None = None) -> GameState:
        self.turn += 1
        return self.adapter.advance(days if days is not None else self.config.days_per_turn)

    def close(self) -> None:
        self.adapter.close()
