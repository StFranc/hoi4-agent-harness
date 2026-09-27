# Evaluation

Five commands, each answering a different question.

| Command | Question |
|---|---|
| `hoi4-harness eval [SCENARIO]` | Did the agent do the thing this scenario asks? |
| `hoi4-harness compare SCENARIO --models P:M,...` | Which model does it better, and for how much? |
| `... --seeds N` (on either) | Is that a result, or one lucky run? |
| `hoi4-harness experiment SCENARIO` | Does model + game AI beat either alone? ([hybrid-control.md](hybrid-control.md#the-experiment)) |
| `hoi4-harness measure` | What does a campaign cost to play, in peace and in war? |

## Scenarios and the baseline

A scenario is a fixed start, an end **date**, and objectives that can be checked
from the game state — some at the end, some over the whole run ("a focus was
running on 80%+ of turns"). Six ship in `eval/scenarios.py`. Every score is
printed against the recorded reflex-only baseline (`eval --no-llm
--write-baseline`): the reflex layer plays for free, so a model is only
interesting where it beats it.

## Comparing models

`compare` runs the same scenario once per model and prints one table, with the
baseline as a row, spend beside score, and the fixed configuration (scenario,
seed, guidance pack, control mode, pacing) above it. Guidance moves scores more
than model choice at the small end, so an unstated pack makes every row
uninterpretable.

## Variance

One run of one scenario against one model is an anecdote. `--seeds N` runs each
configuration at N consecutive seeds starting from the scenario's own, and
reports the median with its range:

```
$ hoi4-harness eval economy_ramp --seeds 5
economy_ramp: median 0.25 [0.25-0.25] over 5 seeds (baseline 0.25, +0.00)
  [0/5] civ_factories_20
  [5/5] stability_held
  ...
```

On `compare`, rows gain a range column and the cost columns come from the
median-scoring run, so score and spend in one row describe the same run. Each
seed writes its own transcript under `seed-<n>/`.

What varies between seeds is what the harness can vary: the mock's RNG today,
the game's own once the harness drives a real game. A model sampling at
temperature varies on its own, which is the variance this mostly exists to
expose.

**Rule of thumb for a claim that goes in a README:**

- at least **five seeds** per configuration — fewer and the output labels itself
  "an anecdote, not a result";
- report **median and range**, never a mean on its own;
- "A beats B" needs, at the very least, A's worst run to beat B's median;
  ranges that overlap heavily are a tie whatever the medians say;
- the baseline's range counts too — a model whose median sits inside the
  reflexes' range has not shown it is worth waking.

## Measuring cost

`measure` reads a transcript — or plays a long measurement campaign first with
`--campaign peacetime_1936_1939` / `wartime_1939_1941` — and reports per phase:
game days, wakes and wakes per game-month, brief size (full and delta apart), the
request size split into cacheable prefix and per-turn message, and, for a real
provider, billed input tokens per wake and the cache hit rate. The results and
their caveats are in [cost-and-timing.md](cost-and-timing.md#the-arithmetic).
