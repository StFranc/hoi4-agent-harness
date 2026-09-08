"""The agent loop.

One iteration is one *decision point*, not one game-day:

    read state -> should this wake the planner?
      no  -> run reflex actions, advance the clock, repeat (free)
      yes -> build an observation, call the model, run its tools, advance

Within a wake, the model gets a small number of tool rounds so it can react to a
rejected action, then the turn ends whether or not it called advance_time. The
harness always takes the clock back.

With ``planner_enabled`` False the wake rule never fires: every turn takes the
reflex path, no model is ever called, and the run is the baseline that scored
runs are reported against.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions import catalog, registry
from ..config import HarnessConfig
from ..env import HOI4Env
from ..types import ActionCall, ActionResult
from .advisor import Advisor
from .budget import BudgetGuard
from .errors import classify, describe
from .llm.base import LLMClient, Msg, ToolResult
from .memory import Memory
from .policy import Policy
from .prompts import build_system, turn_prompt
from .resume import rebuild


@dataclass
class RunReport:
    turns: int = 0
    planner_calls: int = 0
    reflex_turns: int = 0
    actions_ok: int = 0
    actions_failed: int = 0
    llm_errors: int = 0
    start_date: str = ""
    end_date: str = ""
    spend: dict = field(default_factory=dict)
    transcript_path: str | None = None
    #: Set when the run ended early. A completed run leaves this None, so a
    #: caller can tell "played 200 turns" from "gave up after 3".
    stopped_reason: str | None = None
    resumed_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


class Transcript:
    """Append-only JSONL, one record per event. The eval runner reads these."""

    def __init__(self, path: Path | None, append: bool = False):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not append:
                path.write_text("", encoding="utf-8")
            elif not path.exists():
                path.touch()

    def write(self, kind: str, **payload) -> None:
        if not self.path:
            return
        record = {"t": round(time.time(), 3), "kind": kind, **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")


class AgentLoop:
    def __init__(
        self,
        env: HOI4Env,
        planner: LLMClient | None,
        config: HarnessConfig | None = None,
        memory: Memory | None = None,
        transcript_path: Path | None = None,
        resume: bool = False,
    ):
        self.env = env
        self.config = config or HarnessConfig()
        if planner is None and self.config.planner_enabled:
            raise ValueError(
                "planner_enabled is on but no planner was given: pass a client, or set "
                "planner_enabled=False for a reflex-only run"
            )
        self.planner = planner
        self.memory = memory or Memory()
        self.policy = Policy(
            days_per_turn=self.config.days_per_turn,
            wake_on_no_focus=self.config.wake_on_no_focus,
            wake_on_free_research_slot=self.config.wake_on_free_research_slot,
            reflex_enabled=self.config.reflex_enabled,
            planner_enabled=self.config.planner_enabled,
        )
        self.system = build_system(
            guidance=self.config.guidance,
            extra=self.config.system_prompt_extra,
            system_prompt_path=self.config.system_prompt_path,
            operational_control=self.config.operational_control,
            advisor=self.config.advisor,
        )
        self.budget = BudgetGuard(self.config.budget)
        self.transcript = Transcript(transcript_path, append=resume)
        self.advisor = (
            Advisor(transcript=self.transcript, memory=self.memory) if self.config.advisor else None
        )
        self.report = RunReport(transcript_path=str(transcript_path) if transcript_path else None)
        self.run_dir = transcript_path.parent if transcript_path else None
        self._consecutive_errors = 0

        if resume and transcript_path:
            self._resume_from(transcript_path)

    def _resume_from(self, transcript_path: Path) -> None:
        """Carry forward what the interrupted run knew.

        Spend is restored too, so a resumed run cannot quietly double its budget
        by forgetting what it already spent.
        """
        state = rebuild(transcript_path)
        if not state.records:
            return
        self.memory = state.memory if state.memory.journal or state.memory.digest else self.memory
        self.report.turns = state.turns
        self.report.planner_calls = state.planner_calls
        self.report.reflex_turns = state.reflex_turns
        self.report.actions_ok = state.actions_ok
        self.report.actions_failed = state.actions_failed
        self.report.llm_errors = state.llm_errors
        self.report.start_date = state.start_date
        self.report.resumed_from = str(transcript_path)
        self.budget.spend.calls = state.planner_calls
        self.budget.spend.usage = state.usage
        self.budget.spend._usd = state.usd
        self.env.turn = state.turns
        self.transcript.write("resumed", **{"summary": state.summary(), "records": state.records})

    # --- public --------------------------------------------------------------

    def run(self, turns: int | None = None) -> RunReport:
        turns = turns if turns is not None else self.config.turns
        state = self.env.read_state()
        if not self.report.start_date:
            self.report.start_date = state.date
        days_since_planner = self.config.days_per_turn  # wake on the first iteration

        for _ in range(turns):
            state = self.env.read_state()
            decision = self.policy.should_wake(state, days_since_planner)
            blocked = self.budget.why_blocked()

            if decision.wake and blocked:
                self.transcript.write("budget_downgrade", reason=blocked, date=state.date)
                decision.wake = False

            if decision.wake:
                self._planner_turn(decision.reason)
                days_since_planner = 0
            else:
                self._reflex_turn(decision.reason)
                days_since_planner += self.config.days_per_turn

            state = self.env.advance()
            self.report.turns += 1
            self.report.end_date = state.date
            self.transcript.write("turn_end", date=state.date, turn=self.report.turns)
            self._checkpoint()

            if self.report.stopped_reason:
                break

        self.report.spend = self.budget.summary()
        self.transcript.write("run_end", **self.report.to_dict())
        return self.report

    # --- one turn ------------------------------------------------------------

    def _reflex_turn(self, reason: str) -> None:
        self.report.reflex_turns += 1
        state = self.env.read_state()
        calls = self.policy.reflex_actions(state)
        if self.advisor:
            # Reflex housekeeping would be acting; in advisor mode the human
            # owns every action. The clock still advances in run(), so the
            # turn simply costs nothing.
            calls = []
        if not calls:
            self.transcript.write("skip", date=state.date, reason=reason)
            return
        results = self.env.act_many(calls)
        self._tally(results)
        self.transcript.write(
            "reflex",
            date=state.date,
            reason=reason,
            actions=[c.to_dict() for c in calls],
            results=[r.to_dict() for r in results],
        )

    def _planner_turn(self, reason: str) -> None:
        observation = self.env.observe()
        # Adapters hand back a live reference to their own GameState, which keeps
        # mutating as this turn's actions run. Capture the date now: journalling
        # with observation.state.date at the end of the turn dated every entry
        # after whatever advance_time the model called, putting the memory a week
        # ahead of the turn it describes.
        turn_date = observation.state.date
        if self.advisor:
            # The menu is not gated on adapter capability: nothing executes, so
            # a read-only adapter that supports nothing is still a fine advisor
            # seat. Both hybrid vocabularies are fair as advice, so only the
            # operator's whitelist narrows the full catalog.
            allowed = set(catalog.names()) - set(self.config.disabled_actions)
            if self.config.enabled_actions is not None:
                allowed &= set(self.config.enabled_actions)
            tools = registry.tool_specs(allowed)
            self.advisor.begin(date=turn_date, turn=observation.turn, wake_reason=reason)
        else:
            tools = registry.tool_specs(self.env.allowed_actions)
        messages: list[Msg] = [
            Msg(
                role="user",
                text=turn_prompt(
                    brief=observation.brief,
                    objective=self.config.objective,
                    memory_block=self.memory.context_block(),
                    wake_reason=reason,
                    turn=self.env.turn,
                    actions_left=self.config.max_actions_per_turn,
                ),
            )
        ]
        self.transcript.write(
            "observe",
            date=turn_date,
            turn=observation.turn,
            is_delta=observation.is_delta,
            brief=observation.brief,
            wake_reason=reason,
        )

        taken: list[str] = []
        for _round in range(self.config.max_tool_rounds_per_turn):
            if not self.budget.allows_call():
                break
            try:
                response = self.planner.complete(self.system, messages, tools)
            except Exception as exc:  # noqa: BLE001 - the provider is not ours
                self._record_llm_failure(exc, turn_date)
                self._reflex_fallback(f"model call failed: {type(exc).__name__}")
                return
            self._consecutive_errors = 0
            self.budget.record(response.usage)
            self.report.planner_calls += 1
            self.transcript.write(
                "llm",
                model=response.model,
                stop_reason=response.stop_reason,
                text=response.text,
                tool_calls=[c.__dict__ for c in response.tool_calls],
                usage=response.usage.__dict__,
            )

            if self.advisor and response.text:
                self.advisor.prose(response.text)

            if not response.tool_calls:
                break

            messages.append(Msg(role="assistant", text=response.text,
                                tool_calls=response.tool_calls))

            results: list[ToolResult] = []
            end_turn = False
            for call in response.tool_calls:
                action = ActionCall(name=call.name, arguments=call.arguments, call_id=call.id)
                if self.advisor:
                    # Intercepted: recorded, shown to the player, never executed.
                    # note is the one call that is processed (into the journal),
                    # which is what keeps the next wake's advice coherent.
                    results.append(
                        self.advisor.receive(
                            action, date=turn_date,
                            turn=observation.turn, wake_reason=reason,
                        )
                    )
                    taken.append(action.name if action.name == "note" else f"{action.name} (recommended)")
                    if action.name == "advance_time":
                        end_turn = True
                    continue
                if action.name == "note":
                    self.memory.note(
                        turn_date, observation.turn, str(action.arguments.get("text", ""))
                    )
                result = self.env.act(action)
                self._tally([result])
                taken.append(f"{action.name}{'' if result.ok else ' (rejected)'}")
                results.append(
                    ToolResult(
                        call_id=call.id,
                        name=call.name,
                        content=result.as_tool_content(),
                        is_error=not result.ok,
                    )
                )
                if action.name == "advance_time":
                    end_turn = True

            if not self.advisor:
                self.transcript.write("actions", results=[r.__dict__ for r in results])
            if end_turn:
                break
            messages.append(Msg(role="user", tool_results=results))

        self.memory.record_turn(turn_date, taken)

    # --- failure handling ----------------------------------------------------

    def _record_llm_failure(self, exc: BaseException, date: str) -> None:
        """Log the failure and decide whether the run can continue.

        A fatal error will fail identically on every later call, so grinding on
        produces a long transcript of nothing. A transient one is worth riding
        out -- but only so many in a row, since an unrecognised permanent
        failure is classified transient by default.
        """
        kind = classify(exc)
        self.report.llm_errors += 1
        self._consecutive_errors += 1
        self.transcript.write(
            "llm_error", date=date, error_kind=kind, error=describe(exc),
            consecutive=self._consecutive_errors,
        )

        if kind == "fatal":
            self.report.stopped_reason = f"fatal provider error: {describe(exc)}"
        elif self._consecutive_errors >= self.config.max_consecutive_llm_errors:
            self.report.stopped_reason = (
                f"{self._consecutive_errors} consecutive provider errors; "
                f"last was {describe(exc)}"
            )

    def _reflex_fallback(self, reason: str) -> None:
        """Play the turn for free rather than losing it."""
        state = self.env.read_state()
        calls = self.policy.reflex_actions(state)
        if not calls:
            self.transcript.write("skip", date=state.date, reason=reason)
            return
        results = self.env.act_many(calls)
        self._tally(results)
        self.transcript.write(
            "reflex", date=state.date, reason=reason,
            actions=[c.to_dict() for c in calls],
            results=[r.to_dict() for r in results],
        )

    def _checkpoint(self) -> None:
        """Persist memory after every turn, not once at the end.

        Cheap -- the file is small and this runs once per turn, not per tick --
        and it is the difference between losing five hours and losing one turn.
        """
        if not self.run_dir:
            return
        try:
            self.memory.save(self.run_dir / "memory.json")
        except OSError:
            # A checkpoint failure must not end a campaign; the transcript is
            # still the authority and can rebuild this.
            self.transcript.write("checkpoint_failed", path=str(self.run_dir))

    def _tally(self, results: list[ActionResult]) -> None:
        for result in results:
            if result.ok:
                self.report.actions_ok += 1
            else:
                self.report.actions_failed += 1
