"""The agent loop.

One iteration is one *decision point*, not one game-day:

    read state -> should this wake the planner?
      no  -> run reflex actions, advance the clock, repeat (free)
      yes -> build an observation, call the model, run its tools, advance

Within a wake, the model gets a small number of tool rounds so it can react to a
rejected action, then the turn ends whether or not it called advance_time. The
harness always takes the clock back.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions import registry
from ..config import HarnessConfig
from ..env import HOI4Env
from ..types import ActionCall, ActionResult
from .budget import BudgetGuard
from .llm.base import LLMClient, Msg, ToolResult
from .memory import Memory
from .policy import Policy
from .prompts import build_system, turn_prompt


@dataclass
class RunReport:
    turns: int = 0
    planner_calls: int = 0
    reflex_turns: int = 0
    actions_ok: int = 0
    actions_failed: int = 0
    start_date: str = ""
    end_date: str = ""
    spend: dict = field(default_factory=dict)
    transcript_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


class Transcript:
    """Append-only JSONL, one record per event. The eval runner reads these."""

    def __init__(self, path: Path | None):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

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
        planner: LLMClient,
        config: HarnessConfig | None = None,
        memory: Memory | None = None,
        transcript_path: Path | None = None,
    ):
        self.env = env
        self.planner = planner
        self.config = config or HarnessConfig()
        self.memory = memory or Memory()
        self.policy = Policy(
            days_per_turn=self.config.days_per_turn,
            wake_on_no_focus=self.config.wake_on_no_focus,
            wake_on_free_research_slot=self.config.wake_on_free_research_slot,
            reflex_enabled=self.config.reflex_enabled,
        )
        self.system = build_system(
            guidance=self.config.guidance,
            extra=self.config.system_prompt_extra,
            system_prompt_path=self.config.system_prompt_path,
            operational_control=self.config.operational_control,
        )
        self.budget = BudgetGuard(self.config.budget)
        self.transcript = Transcript(transcript_path)
        self.report = RunReport(transcript_path=str(transcript_path) if transcript_path else None)

    # --- public --------------------------------------------------------------

    def run(self, turns: int | None = None) -> RunReport:
        turns = turns if turns is not None else self.config.turns
        state = self.env.read_state()
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

        self.report.spend = self.budget.summary()
        self.transcript.write("run_end", **self.report.to_dict())
        return self.report

    # --- one turn ------------------------------------------------------------

    def _reflex_turn(self, reason: str) -> None:
        self.report.reflex_turns += 1
        state = self.env.read_state()
        calls = self.policy.reflex_actions(state)
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
            date=observation.state.date,
            turn=observation.turn,
            is_delta=observation.is_delta,
            brief=observation.brief,
            wake_reason=reason,
        )

        taken: list[str] = []
        for _round in range(self.config.max_tool_rounds_per_turn):
            if not self.budget.allows_call():
                break
            response = self.planner.complete(self.system, messages, tools)
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

            if not response.tool_calls:
                break

            messages.append(Msg(role="assistant", text=response.text,
                                tool_calls=response.tool_calls))

            results: list[ToolResult] = []
            end_turn = False
            for call in response.tool_calls:
                action = ActionCall(name=call.name, arguments=call.arguments, call_id=call.id)
                if action.name == "note":
                    self.memory.note(
                        observation.state.date, observation.turn, str(action.arguments.get("text", ""))
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

            self.transcript.write("actions", results=[r.__dict__ for r in results])
            if end_turn:
                break
            messages.append(Msg(role="user", tool_results=results))

        self.memory.record_turn(observation.state.date, taken)

    def _tally(self, results: list[ActionResult]) -> None:
        for result in results:
            if result.ok:
                self.report.actions_ok += 1
            else:
                self.report.actions_failed += 1
