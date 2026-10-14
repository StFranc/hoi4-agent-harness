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
long the model takes across ~190 wakes, one a week (see below).

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

The review interval is `days_per_turn`, which is also how far each turn runs the
clock, so a turn that nothing else wakes the model for is a scheduled review. The
reflex layer plays a turn on its own when the budget has run out, when the
planner is disabled (the reflex-only baseline), or when the clock came back early
and fewer than `days_per_turn` game days have passed since the last review.

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
1936–1939. It is now measured: the estimate was high on the size of a wake and
about right on the number.

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
the transcript has them and dashes when it does not. One caveat is the mock's
own: a planner turn that ends in `advance_time` runs the model's days and then
the harness's `days_per_turn` on top (#65), so most of the scripted planner's
turns are 14 days long, not 7. Every wake count in the table is per those longer
turns.

| campaign, wake rules | phase | game days | turns | wakes | wakes / month | brief mean (full / delta) | brief max | request: prefix + turn |
|---|---|---|---|---|---|---|---|---|
| peacetime, default | peace | 1,339 | 110 | 110 | 2.5 | 44 (129 / 31) | 139 | 1,531 + 176 |
| peacetime, schedule only | peace | 1,339 | 110 | 110 | 2.5 | 44 (129 / 31) | 139 | 1,531 + 174 |
| wartime, default | war | 861 | 76 | 76 | 2.7 | 67 (194 / 46) | 255 | 1,531 + 234 |
| wartime, schedule + alarms | war | 861 | 76 | 76 | 2.7 | 67 (194 / 46) | 255 | 1,531 + 233 |

What that says:

- **A wake sends ~1.7k tokens, not ~2.2k.** ~1.5k of it is the cacheable
  prefix, so the design's premise holds: the part that changes turn to turn is
  ~180 tokens, and most of that is the memory block and framing, not the brief.
- **Every turn is a review.** Wakes equal turns in all four runs. A review is
  due every `days_per_turn`, and a turn runs at least that long unless a
  critical event cuts it short and wakes the model anyway, so the opportunity
  rules only change why a turn wakes the model (in the default peacetime run: an
  idle research slot 48 times, no focus 28, the schedule 34), never whether it
  does.
- **1936–1939 is ~190 wakes at a weekly review, so the ~190 estimate was about
  right.** The 110 above is over the mock's turns, three in four of them 14 days
  long. At the 7-day turn the real input driver runs, the campaign is
  1,339 / 7 ≈ 191 turns and each is a review. With the mock's `advance_time`
  handing the clock back as #65 proposes, the same command measures 192 turns,
  and the default `max_llm_calls` of 250 runs out first: the scripted planner
  makes ~1.5 calls a wake, so 174 turns wake the model and the last 18, from
  May 1939, drop to reflexes. At ~1.7k input a wake, ~190 wakes (with a ceiling
  that allows them) is ~325k input tokens for the campaign, ~290k of them
  cacheable, against the ~400k estimated (the 110 wakes measured above are
  ~190k, ~170k cacheable).
- **War did not compound.** The full brief grows by half (129 → 194) and the
  per-turn message by a third (176 → 234), because fronts are capped at three
  lines. The wake rate goes from 2.5 to 2.7 a month, and since every turn is a
  review that is the turn rate: more of the scripted planner's war turns end in
  orders rather than `advance_time`, so fewer run the mock's extra week, and a
  critical event cuts one short. In the schedule-and-alarms run the alarms name
  6 of 76 wartime wakes (three critical events, the supply collapse in the
  October mud, and the losing front after the counteroffensive, twice); with a
  review due every turn they change the reason for a wake more than the count.
- **What the estimate got right:** the order of magnitude, the number of wakes,
  and that the prefix is most of the bill.

Reproduce with:

```bash
hoi4-harness measure --campaign peacetime_1936_1939
hoi4-harness measure --campaign wartime_1939_1941
echo '{"wake_on_free_research_slot": false, "wake_on_no_focus": false}' > sched.json
hoi4-harness measure --campaign peacetime_1936_1939 --profile sched.json
hoi4-harness measure --campaign wartime_1939_1941 --profile sched.json
hoi4-harness measure runs/transcript.jsonl       # or any transcript from play/eval
```

At frontier-model prices that lands around a dollar or two per campaign; on a
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
