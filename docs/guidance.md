# Guidance: how much to tell the model

The system prompt has two halves and only one of them is fixed.

**Mechanics** are fixed: what the tools are, what a turn is, what "unknown" in a
report means, what to do when an action is rejected. Remove that and the model
cannot operate the harness.

**Guidance** — how to actually play — is a file you swap. It is the most
interesting variable in this project. A model given no doctrine and a model given
a prescriptive checklist are two different experiments, and the harness should
not quietly decide which one you are running.

```bash
hoi4-harness prompt --guidance none            # see exactly what gets sent
hoi4-harness play   --guidance unconstrained
hoi4-harness play   --guidance ./my-doctrine.md
hoi4-harness play   --system-prompt ./whole-thing.txt   # replace everything
```

## Built-in packs

| Pack | What it says |
|---|---|
| `none` | Nothing. Form your own view of the game. |
| `unconstrained` | Play to win by any in-game means. Ahistorical, opportunistic, exploit-friendly. Explicitly not scored on restraint. |
| `minimal` | Three load-bearing mechanics facts, no strategy. |
| `doctrine` | Opinionated general advice. The default. |
| `historical` | Stay close to what the country actually did. |
| `coach` | A prescriptive numbered checklist, for smaller or weaker models. |

They live in [`src/hoi4_harness/guidance/`](../src/hoi4_harness/guidance/) as plain
markdown. Copy one, edit it, point `--guidance` at it.

## Choosing one

- **Benchmarking a model's game sense**: `none` or `minimal`. Anything more and
  you are benchmarking the doctrine you wrote.
- **Getting a small local model to play coherently**: `coach`, plus a narrowed
  action list. A 7B model with six actions and a checklist plays; the same model
  with sixteen actions and no scaffold flails.
- **Letting a strong model off the leash**: `unconstrained`, `--no-reflex`,
  `--allow-all`, `--live`. See [`profiles/unleashed.toml`](../profiles/unleashed.toml).
- **A comparison you can publish**: hold guidance fixed across models and say
  which pack you used. Guidance moves scores more than model choice does at the
  small end.

## In-game conduct is not constrained

The guidance layer coaches strategy; it does not police the agent. There is no
ban on ahistorical play, opportunistic wars, puppeting, mechanical exploits, or
anything else the game itself permits — `unconstrained` says so out loud, and the
other packs simply do not raise it.

The constraints that *do* exist are structural, and they are about the harness
being trustworthy rather than the agent being well-behaved:

- **Validation** — a call that does not match the schema is rejected with a
  reason, because a malformed call is a bug, not a strategy.
- **The confirmation gate** — irreversible actions are blocked in dry-run mode,
  and route through `confirm_hook` when live. Answer them yourself with
  `--confirm`, or turn the gate off with `--allow-all`.
- **Budget ceilings** — money, not morals.
