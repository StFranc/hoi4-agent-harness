# Cost and timing

Two objections come up immediately when you propose an LLM agent for HOI4: the
game runs in real time, and the state space is enormous. Both are real. Neither
is fatal, and the answers are structural rather than clever.

## 1. Real time is optional

HOI4 pauses, and it pauses instantly and completely. The loop therefore runs:

```
pause -> read state -> decide -> apply actions -> unpause N in-game days -> pause
```

The model never races the clock. Wall-clock latency stops mattering: a model that
takes forty seconds to think just means the game sits paused for forty seconds.
What *does* matter is in-game latency — how many game-days pass between a thing
happening and the agent noticing — and that is bounded two ways: a scheduled
review every `days_per_turn` (default 7), and an early return from `advance()`
the moment a critical event fires.

Speed 5 on a fast machine runs roughly a game-week in a couple of seconds, so a
1936–1939 campaign at weekly reviews is a few minutes of game time plus however
long the model takes across ~105 wakes (measured; see below).

## 2. Most game-days are not decisions

This is the part that decides the bill. Asking a model "what now?" every game-day
across 1936–1939 is ~1,300 calls to answer "keep building" nine hundred times.

The harness splits control:

- **Reflex** ([`agent/policy.py`](../src/hoi4_harness/agent/policy.py)) — deterministic Python. Refills an
  empty construction queue, reassigns idle military factories, and nothing that
  requires judgement. Free and instant.
- **Planner** — the model. Woken on: a critical event, no national focus running,
  an idle research slot, a front losing ground, or the scheduled review. At war,
  also on the *onset* of an encirclement, a supply collapse on a front that was
  supplied last turn, the capital coming under threat, an ally capitulating or
  leaving the war, and a front going quiet. Onset, not condition: a pocket wakes
  the model when it opens or grows, not every turn it stands.

Widening that rule is how a run gets expensive. It is deliberately one small
function so the cost of a change is visible.

## 3. The state space is compressed before it is sent

A HOI4 save is tens of megabytes. Almost none of it is decision-relevant on any
given turn. Three reductions, in order:

1. **Normalise** — the adapter maps whatever it can see onto `GameState`, one flat
   struct of ~25 fields plus five short lists. Anything it cannot see is recorded
   in `unknown_fields` rather than defaulted to zero.
2. **Render** — `render_full` produces a ~300-token brief: numbers, not prose,
   empty sections omitted, long lists truncated to what matters, and the
   decision-shaped facts shouted (`NONE SELECTED`, `3 SLOT(S) FREE`, `QUEUE EMPTY`).
3. **Diff** — after the first brief, `render_delta` sends only what moved, with
   per-field thresholds so noise (manpower ticking up 120/day) never appears.
   A full brief returns when the diff grows past ~12 lines, every
   `full_brief_every` turns, or on any critical event.

## 4. The prompt is built for caching

Prefix caching only pays if the prefix is byte-identical. So:

- `system` is a frozen constant — role, doctrine, rules. No dates, no numbers.
- The tool block is generated deterministically from the catalog, filtered by
  adapter capability, in stable order.
- Everything volatile — brief, memory, wake reason — is in the last user message.

With the Anthropic provider both the system block and the tool block are marked
cacheable, so from the second call onward the ~1.5k-token prefix (measured) bills at
cache-read rates.

## 5. Memory instead of transcript

Each wake starts a fresh conversation. Continuity comes from two bounded things:

- a **journal** the agent writes itself through the `note` action (last 12 entries),
- a **digest** of recent turns (last 6 lines, auto-generated).

A growing transcript would be both more expensive and worse: old briefs
contradict the current one, and the model re-argues settled decisions.

## The arithmetic

This section started as an estimate: ~2.2k input tokens a wake, ~190 wakes for
1936–1939. It is now measured, and the estimate was high on both counts.

**Method.** `hoi4-harness measure --campaign NAME` plays a long campaign against
the mock and reads its transcript. Every wake records the brief's size, what the
first request of the wake sends (split into the cacheable prefix — system prompt
plus tool schemas — and the per-turn message), whether the country was at war,
and why the model was woken. Two campaigns: neutral Sweden 1936-01-01 to
1939-08-31, and Germany 1939-08-01 to 1941-12-31 with its wars scripted onto the
mock (fronts opening and closing on their dates, the autumn mud, the December
counteroffensive — see `eval/campaigns.py`). The planner is the scripted
stand-in, so each is run twice: with the default wake rules, and with the two
opportunity rules off, since the stand-in fills one research slot per wake and
so inflates the idle-slot rule a real model would not.

**Caveats, stated rather than buried.** Token counts here are characters / 4:
the container this was measured in could not fetch a tokenizer. With `tiktoken`
installed the same command counts properly and the transcript says which method
it used. The mock's war is a script, not a simulation, and the scripted planner
is not a model, so wake *rate* is the harness's rule applied to the mock's
signals — the number to re-measure on a real game (#1, #3) and a real model.
Billed tokens and cache hits need a real provider; the command prints them when
the transcript has them and dashes when it does not.

| campaign, wake rules | phase | game days | wakes | wakes / month | brief mean (full / delta) | brief max | request: prefix + turn |
|---|---|---|---|---|---|---|---|
| peacetime, default | peace | 1,339 | 105 | 2.4 | 50 (133 / 37) | 151 | 1,531 + 184 |
| peacetime, schedule only | peace | 1,346 | 75 | 1.7 | 56 (131 / 45) | 143 | 1,531 + 189 |
| wartime, default | war | 854 | 66 | 2.4 | 70 (175 / 53) | 230 | 1,531 + 233 |
| wartime, schedule + alarms | war | 854 | 49 | 1.7 | 77 (195 / 54) | 253 | 1,531 + 233 |

What that says:

- **A wake sends ~1.75k tokens, not ~2.2k.** ~1.5k of it is the cacheable
  prefix, so the design's premise holds: the part that changes turn to turn is
  ~200 tokens, and most of that is the memory block and framing, not the brief.
- **1936–1939 is ~105 wakes, not ~190** — the estimate was ~1.8x high. At 1.75k
  input a wake that is ~185k input tokens for the campaign, ~160k of them
  cacheable, against the ~400k estimated.
- **War did not compound.** The full brief grows by a third (133 → 175) and the
  per-turn message by a quarter (184 → 233), because fronts are capped at three
  lines. The wake rate is flat (2.4 → 2.4, and 1.7 → 1.7 with only the schedule
  and the wartime alarms), because every wartime rule fires on the onset of its
  condition. In the schedule-and-alarms run the alarms account for 5 of 49
  wartime wakes: three critical events, the supply collapse in the October mud,
  and the losing front after the counteroffensive.
- **What the estimate got right:** the order of magnitude, and that the prefix is
  most of the bill.

Reproduce with:

```bash
hoi4-harness measure --campaign peacetime_1936_1939
hoi4-harness measure --campaign wartime_1939_1941
echo '{"wake_on_free_research_slot": false, "wake_on_no_focus": false}' > sched.json
hoi4-harness measure --campaign wartime_1939_1941 --profile sched.json
hoi4-harness measure runs/transcript.jsonl       # or any transcript from play/eval
```

At frontier-model prices that lands around a dollar per campaign; on a
mid-tier model it is cents; on a local model behind an OpenAI-compatible endpoint
it is electricity. Set `--max-usd` and the harness enforces it — on exhaustion the
run does not stop, it drops to the reflex layer and keeps playing for free.

## What is counted where

Providers disagree about what their own usage fields mean, so each provider maps
onto one definition (`Usage` in `agent/llm/base.py`) and the budget prices that:

| | Anthropic | OpenAI / compatible | Gemini |
|---|---|---|---|
| `input_tokens` = all billed input | `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens` | `prompt_tokens` (already includes cached) | `prompt_token_count` (already includes cached) |
| `cached_input_tokens` | `cache_read_input_tokens` | `prompt_tokens_details.cached_tokens` (often absent on local endpoints: reads as 0) | `cached_content_token_count` |
| `cache_write_input_tokens` | `cache_creation_input_tokens` | 0 — caching is implicit | 0 — implicit caching |
| `output_tokens`, reasoning included | `output_tokens` | `completion_tokens` | `candidates_token_count` + `thoughts_token_count` |

Two of those were wrong until they were written down. Anthropic's
`input_tokens` is only the uncached remainder, so a run's input total was short
by its whole cached prefix and cache writes were never counted at all. Gemini's
`candidates_token_count` leaves out thinking, which bills as output.

Dollars are then `uncached × input rate + cache reads × cached rate + cache
writes × write rate + output × output rate`. The cached and write rates default
to the input rate — overstating a cached run rather than calling its prefix
free — so set all four from the price sheet (`HOI4_USD_PER_M_*`, or `budget.*`
in a profile). `doctor` warns when a real provider would run unpriced, since
`$0.00` then means "nobody set a rate", not "free".

Not yet done: checking a real run's reported spend against a provider's own
usage dashboard. That needs a key and a run, and is the remaining half of #23.

## What would break this

Honest failure modes, since they decide whether the numbers above survive contact:

- **Wartime.** Fronts, combats, and encirclements are the state that does not
  compress well, and war is exactly when reflexes are least adequate. Expect the
  wake rate and brief size to rise together. Brief size is now capped:
  `observation/fronts.py` shows at most three fronts in full, ranked by how much
  they need a decision (losing ground, a pocket forming, the capital threatened,
  supply failing, an attack), and folds the rest into one line that still says
  whether anything in the fold is in trouble. A synthetic twelve-front war renders
  as a ~450-character brief. Measured over a scripted 1939–1941 (above), neither
  brief size nor wake rate compounded; a real game is what would falsify that.
- **Screen-based observation.** A vision call per observation is 1–2k image tokens
  and cannot be cached across turns. It is for what numbers cannot express, not
  for routine ticks.
- **A chatty model.** A model that narrates instead of acting can double output
  tokens. `max_tool_rounds_per_turn` caps the damage; the prompt's "take the
  smallest set of actions, then advance_time" is doing real work.
