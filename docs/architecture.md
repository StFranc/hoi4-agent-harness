# Architecture

```
                +-----------------------------------------------+
                |                  AgentLoop                    |
                |  wake? -> observe -> model -> tools -> advance |
                +---+-------------+--------------+--------------+
                    |             |              |
              Policy|       Memory|        Budget|
             (wake rule,    (journal +     (calls, tokens,
              reflexes)      digest)        dollars)
                    |             |              |
                +---v-------------v--------------v---+
                |               HOI4Env              |
                |  validate -> confirm gate -> apply |
                +------------------+-----------------+
                                   |
                          +--------v---------+
                          |   GameAdapter    |
                          | mock | savegame  |
                          | screen | input   |
                          +------------------+
```

## Why these seams

**Adapter.** HOI4 ships no API, so every bridge is an approximation with a
different blind spot: a save file has exact numbers and no liveness, a screenshot
has liveness and no precision, synthetic input can act but cannot see. Rather
than pick one, the contract lets an adapter declare what it can do
(`supported_actions`, `AdapterInfo`) and what it could not see (`unknown_fields`),
and `CompositeAdapter` pairs a reader with a writer.

**Env.** The rules that must not depend on the model behaving live here:
schema validation, the confirmation gate on irreversible actions, the per-turn
action cap, and exception containment (an adapter fault becomes a failed action,
not a dead run).

**Policy.** The wake rule and the reflexes. See
[cost-and-timing.md](cost-and-timing.md) — this file is the cost model.

**Observation.** State to prompt text, full or delta.

**LLM layer.** A neutral message/tool dialect (`Msg`, `ToolCall`, `ToolResult`,
`ToolSpec`) that each provider maps to its own wire format. Nothing above this
layer knows which vendor is answering, which is what makes "run the same scenario
on four models" a config change.

## One turn, in detail

1. `env.read_state()` — cheap, no model involved.
2. `policy.should_wake(state, days_since_planner)` → wake or not, plus a reason.
3. Not woken: run `policy.reflex_actions(state)`, log, advance the clock.
4. Woken but out of budget: same as (3), plus a `budget_downgrade` record.
5. Woken: build an `Observation` (full or delta), assemble the prompt (frozen
   system + volatile user message), call the model with the filtered tool list.
6. For each tool call: validate → gate → apply → return a compact result. `note`
   also lands in memory. `advance_time` ends the turn.
7. Up to `max_tool_rounds_per_turn` rounds, so the model can react to a rejection
   without being able to loop forever.
8. Record the turn in memory, advance the clock, repeat.

Everything above is written to a JSONL transcript, one record per event, which is
what the eval runner and any later analysis read.

## Surviving a long run

Two failure modes only appear once a run is hours long, and both are handled the
same way the budget ceiling is: degrade, do not stop.

**A provider error takes the turn, not the run.** The call is wrapped; the
failure is classified (`agent/errors.py`) from its HTTP status where there is one
and its exception name where there is not. A transient failure -- rate limit,
timeout, 5xx -- costs one turn, which the reflex layer plays, and the run
continues. A fatal one -- bad key, rejected schema -- would fail identically
forever, so the run stops and says why. Unrecognised errors count as transient,
because a wrong "transient" costs a turn while a wrong "fatal" costs the
campaign; consecutive transients are counted, so an unrecognised permanent
failure still terminates after `max_consecutive_llm_errors`.

**A crash costs one turn, not the campaign.** Memory is checkpointed after every
turn, and `--resume` rebuilds the rest from the transcript (`agent/resume.py`):
turn count, spend, the agent's own journal, and the turn digest. Reconstruction
tolerates a transcript that was truncated mid-write, since that is the normal
shape of a file written by a process that died. Resuming appends to the same
transcript rather than truncating it, and carries spend forward -- a resumed run
cannot quietly get a second budget.

Note what resume does *not* do: it restores what the *harness* knew, not the
game. The mock's world is gone when the process dies; a live adapter re-observes
the running game, which is the only case where resume is fully meaningful.

## Extending it

- **A new action**: add an `ActionSpec` to `actions/catalog.py`, then handle it in
  whichever adapters can perform it. Adapters that cannot will refuse it by
  default — no adapter changes needed to stay correct.
- **A new provider**: implement `LLMClient.complete` and register it in
  `agent/llm/__init__.py`. ~120 lines; the three existing providers are the
  reference.
- **A new adapter**: subclass `GameAdapter`, declare `supported_actions`, and be
  honest in `unknown_fields`.
- **A new scenario**: a `Scenario` with objectives that are predicates over
  `GameState`. Scoring reports outcome and cost side by side on purpose.
