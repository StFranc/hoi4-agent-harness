# The LLM Bridge mod

[`mod/llm_bridge/`](../mod/llm_bridge/) is a small HOI4 mod that gives the harness
a real bridge into a running game. It does two jobs:

- **telemetry** — the game prints a structured state line into `game.log` on a
  schedule, which the harness tails live;
- **directives** — decisions and `ai_strategy` blocks that let the agent set
  strategic intent for the native AI (see [hybrid-control.md](hybrid-control.md)).

## Why this beats the alternatives

| Path | Exact | Live | Cost per read | Works on ironman |
|---|---|---|---|---|
| Save file | yes | no (autosave cadence) | parse a large file | no |
| Screen + vision | approximate | yes | a vision call | yes |
| **Mod telemetry** | **yes** | **yes** | **a file read** | no (checksum) |

Tailing a log the game already writes is the cheapest exact reading available.
The mod is the only reason the harness can afford to look at the world often.

## Install

Copy `llm_bridge/` and `llm_bridge.mod` into
`Documents/Paradox Interactive/Hearts of Iron IV/mod/`, enable **LLM Bridge** in
the launcher, then:

```bash
export HOI4_LOG_PATH="$HOME/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
hoi4-harness doctor --adapter logtail
hoi4-harness play --adapter logtail+input --live
```

## The wire format

One pipe-delimited line per tick, prefixed so it can be found in a log full of
engine chatter:

```
LLMB|v1|state|date=1936.2.14|tag=SWE|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=214000
LLMB|v1|evt|tag=SWE|kind=1|detail=Germany declared war
```

Rules the parser holds to, all of them tested in
[`tests/test_logtail.py`](../tests/test_logtail.py):

- **The date comes from the engine, not the mod.** Every log line is stamped
  `[17:34:20][1944.05.16.01][file.cpp:123]` by the game itself, and the parser
  reads that in preference to any `date=` field. One less script token that has
  to be right.
- **Version match or refuse.** `LLMB_SCHEMA_VERSION` in the mod and
  `SCHEMA_VERSION` in `adapters/logtail.py` are compared on every line. A
  mismatch raises rather than guessing.
- **A field that did not expand is missing, not zero.** If the game does not
  recognise a token it prints it literally; the parser catches that and marks the
  field unknown. This is the single most important rule in the file — a
  fabricated `0` for war support is worse than a gap, because the agent will
  reason confidently from it.
- **Fields the mod does not emit yet are declared unknown up front** (research,
  production, construction, fronts, wars, focus).
- **Only new bytes are read**, and a truncated or rotated log resets the offset.
- **Events are drained**, not repeated, so one war declaration wakes the planner
  once.

## What needs verifying before you trust a run

The mod is a skeleton, and the risky part is not the structure — it is the script
tokens. Variable names, scope functions, `on_action` names and `ai_strategy`
types all move between HOI4 versions, and Paradox script fails *silently*: an
unrecognised token prints literally, an unknown `ai_strategy` type is ignored.

So before a run that matters, check against `common/` in your own install:

- the resource variables in `llm_bridge_telemetry.txt`;
- the `on_action` names in `llm_bridge_on_actions.txt`;
- the `ai_strategy` types in `llm_bridge_directives.txt`.

CI runs static checks on the mod — balanced braces, descriptor keys,
localisation for every decision, no decision calling an undefined effect, schema
versions in sync — but no CI can tell you whether a token is real. Only the game
knows that.

## Boundaries

`log` is a stock script effect and `game.log` is a file the game already writes.
Nothing here hooks the engine, reads process memory, or patches the executable —
and it should stay that way; that boundary is the difference between an
automated player and a cheat tool. A checksum-breaking mod is single-player only
regardless, which suits a project that has no business in multiplayer.
