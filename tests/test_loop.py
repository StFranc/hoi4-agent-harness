import json

from hoi4_harness.actions import catalog
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import BudgetConfig, HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import GameState


def make_loop(script=None, **overrides):
    config = HarnessConfig(adapter="mock", turns=6, **overrides)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=ScriptedClient(script=script), config=config), env


def test_a_run_advances_the_calendar_and_takes_actions():
    loop, env = make_loop()
    report = loop.run(6)
    assert report.turns == 6
    assert report.start_date < report.end_date
    assert report.actions_ok > 0


def test_irreversible_actions_are_blocked_in_dry_run():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "diplomacy", {"action": "declare_war", "target": "GER"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(1)
    assert loop.report.actions_failed >= 1


def test_the_local_validator_still_guards_a_client_claiming_strict_support():
    """Strict tool schemas are the provider promising well-formed calls; the
    loop does not take the promise. The same malformed call is rejected here as
    it would be on a provider without strict."""
    script = [
        LLMResponse(
            tool_calls=[
                ToolCall(
                    "1",
                    "queue_construction",
                    {"building": "civilian_factory", "state": "capital", "turbo": True},
                )
            ],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(
            tool_calls=[ToolCall("2", "advance_time", {"days": 7, "reason": "turn over"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
    ]
    loop, env = make_loop(script=script)
    loop.planner.supports_strict_tools = True    # a strict-capable provider
    loop.run(1)
    assert loop.report.actions_failed == 1       # caught locally, not by the provider


def test_the_budget_guard_downgrades_to_reflex_instead_of_stopping():
    loop, env = make_loop()
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    report = loop.run(5)
    assert report.planner_calls <= 1
    assert report.reflex_turns >= 3      # the rest ran for free
    assert report.turns == 5             # and the game kept moving


def test_notes_written_by_the_agent_come_back_in_the_next_prompt():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "note", {"text": "Build civs until 1938."})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(2)
    assert any("Build civs until 1938." in e.text for e in loop.memory.journal)
    assert "Build civs until 1938." in loop.memory.context_block()


def test_the_planner_is_not_woken_when_nothing_is_happening():
    from hoi4_harness.agent.policy import Policy

    quiet = GameState(national_focus="industrial_effort")
    quiet.research = []
    assert not Policy(days_per_turn=7).should_wake(quiet, days_since_planner=1).wake
    assert Policy(days_per_turn=7).should_wake(quiet, days_since_planner=7).wake


def test_memory_is_dated_when_the_turn_happened_not_after_it():
    """Adapters hand back a live GameState that keeps mutating as the turn's
    actions run, so reading the date at the end of the turn dated every journal
    entry after whatever advance_time the model called."""
    loop, env = make_loop()
    start = env.read_state().date
    loop.run(3)

    observed_dates = [entry.split(":")[0] for entry in loop.memory.digest]
    assert observed_dates[0] == start
    # Turns are days_per_turn apart in the mock; nothing should be dated between.
    assert len(set(observed_dates)) == len(observed_dates)
    for entry, expected in zip(loop.memory.digest, observed_dates, strict=True):
        assert entry.startswith(expected)


def test_a_note_is_journalled_under_the_date_of_the_turn_that_wrote_it():
    script = [
        LLMResponse(
            tool_calls=[
                ToolCall("1", "note", {"text": "Plan set."}),
                ToolCall("2", "advance_time", {"days": 30}),
            ],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    start = env.read_state().date
    loop.run(1)
    assert loop.memory.journal[0].date == start
def test_a_wake_turn_leaves_a_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert lines[0].startswith("1936-01-01")
    assert "wake: no national focus is running" in lines[0]
    # Which actions a wake produces depends on what the brief showed, so assert
    # the line reports some and carries the token and cost columns.
    assert any(name in lines[0] for name in (spec.name for spec in catalog.ACTIONS))
    assert " tok" in lines[0]
    assert lines[0].endswith("$0.00")


def test_a_reflex_turn_leaves_a_progress_line_without_a_token_column():
    lines: list[str] = []
    loop, _env = make_loop(wake_on_no_focus=False, wake_on_free_research_slot=False)
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert "wake:" not in lines[1]        # a reflex turn is not a wake
    assert " tok" not in lines[1]         # and spends no tokens
    assert lines[1].endswith("$0.00")


def test_a_budget_downgrade_says_why_in_its_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    loop.run(2)
    assert "reflex (budget: call ceiling reached (1))" in lines[1]
    assert "wake:" not in lines[1]


def _build(call_id: str) -> ToolCall:
    return ToolCall(call_id, "queue_construction",
                    {"building": "civilian_factory", "state": "capital", "count": 1})


def test_the_per_turn_action_cap_binds_the_planner_not_only_the_reflexes():
    """#66: the cap lived only in act_many, which only the reflexes call. The
    planner's calls went through env.act one by one and nothing counted them,
    so a model could run rounds x calls-per-round actions in one turn. Notes
    and advance_time are turn bookkeeping, not actions, and stay free."""
    script = [
        LLMResponse(
            tool_calls=[_build("a1"), _build("a2"), ToolCall("n1", "note", {"text": "civs"}),
                        _build("a3")],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(
            tool_calls=[_build("b1"), _build("b2"), _build("b3"),
                        ToolCall("n2", "note", {"text": "still civs"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
        LLMResponse(
            tool_calls=[_build("c1"), ToolCall("t1", "advance_time", {"days": 7})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        ),
    ]
    loop, env = make_loop(script=script, max_actions_per_turn=4, max_tool_rounds_per_turn=3)

    applied: list[str] = []
    real_apply = env.adapter.apply

    def counting_apply(call):
        applied.append(call.name)
        return real_apply(call)

    env.adapter.apply = counting_apply
    results: list[dict] = []
    real_write = loop.transcript.write

    def capture(kind, **fields):
        if kind == "actions":
            results.extend(fields["results"])
        return real_write(kind, **fields)

    loop.transcript.write = capture
    loop.run(1)

    by_id = {r["call_id"]: json.loads(r["content"]) for r in results}
    # Seven construction calls were sent; exactly four reached the game.
    assert applied.count("queue_construction") == 4
    assert [i for i in ("a1", "a2", "a3", "b1") if by_id[i]["ok"]] == ["a1", "a2", "a3", "b1"]
    for refused in ("b2", "b3", "c1"):
        assert by_id[refused]["ok"] is False
        assert by_id[refused]["error"] == "budget"
    # The notes were journalled and the clock was still handed back.
    assert by_id["n1"]["ok"] and by_id["n2"]["ok"]
    assert by_id["t1"]["ok"]
    assert len(loop.memory.journal) == 2


def test_the_cap_is_counted_per_turn_and_starts_again_on_the_next_wake():
    from hoi4_harness.agent.policy import WakeDecision

    def wake(advance_id):
        return LLMResponse(
            tool_calls=[_build(f"{advance_id}-{i}") for i in range(3)]
            + [ToolCall(advance_id, "advance_time", {"days": 7})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )

    loop, env = make_loop(script=[wake("t1"), wake("t2")], max_actions_per_turn=2)
    loop.policy.should_wake = lambda *a, **k: WakeDecision(True, "test")
    loop.run(2)
    # Two builds and the hand-back on each wake; the second wake is not
    # charged for what the first one spent.
    assert loop.report.planner_calls == 2
    assert loop.report.actions_ok == 2 * (2 + 1)
    assert loop.report.actions_failed == 2 * 1
