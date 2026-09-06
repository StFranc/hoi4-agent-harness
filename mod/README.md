# LLM Bridge (HOI4 mod)

The read side of the harness, done properly.

Parsing save files gives you exact numbers on a delay; screen capture gives you
liveness at the price of a vision call. This mod gives you both: it makes the
game itself emit a structured telemetry line into `game.log` on a schedule you
control, which the harness tails in real time for free.

It also carries the two things the hybrid control model needs — decisions that
map one-to-one onto harness actions, and scripted effects that push
`ai_strategy` directives into the native AI.

## Install

Copy `llm_bridge/` and `llm_bridge.mod` into:

```
Documents/Paradox Interactive/Hearts of Iron IV/mod/
```

Enable **LLM Bridge** in the launcher. Then point the harness at the log:

```bash
hoi4-harness play --adapter logtail
export HOI4_LOG_PATH="$HOME/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
```

`hoi4-harness doctor` reports whether it can find and parse the log.

## What it emits

One pipe-delimited line per tick, prefixed so the parser can find it in a log
full of everything else:

```
LLMB|v1|date=1936.2.14|tag=SWE|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=214000
LLMB|v1|evt|kind=war_declared|from=GER|to=POL
```

`LLMB_SCHEMA_VERSION` in `common/scripted_effects/llm_bridge_telemetry.txt` and
`SCHEMA_VERSION` in `hoi4_harness/adapters/logtail.py` must match. They are
checked at runtime, so a stale mod fails loudly instead of feeding the agent
silently wrong numbers.

## Status

Skeleton, and the parts that are unverified say so in the file that contains
them. The script token names (variables, scope functions, on_action names,
`ai_strategy` types) are the bit that moves between HOI4 versions — check them
against `common/` in your own install before trusting a run. See
[../docs/mod-bridge.md](../docs/mod-bridge.md).

## Why a mod is allowed to do this

`log` is a stock script effect and `game.log` is a file the game already writes.
Nothing here hooks the engine, edits memory, or patches the executable. It is
also single-player only — a checksum-breaking mod is not going into multiplayer
regardless of what we would prefer.
