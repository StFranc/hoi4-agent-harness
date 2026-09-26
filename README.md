# hoi4-agent-harness

A skeleton harness for letting an LLM play **Hearts of Iron IV** — any LLM, through
one interface: Anthropic, OpenAI, anything OpenAI-compatible (Ollama, vLLM,
LM Studio, OpenRouter), Google Gemini, or a scripted stub that runs offline.

This is scaffolding, not a finished bot. The parts that are real are real and
tested; the parts that are stubs say so, in this README and in the file that
contains them.

```
git clone https://github.com/Xocas12/hoi4-agent-harness
cd hoi4-agent-harness && pip install -e ".[dev]"
hoi4-harness play --turns 20        # full offline run, no API key, no game needed
```

## The two problems this is built around

HOI4 is a real-time game with an enormous state space, and language models are
slow and priced per token. Those two facts kill the naive design — screenshot the
screen every second, ask a model what to do — before it starts. Two mechanisms
answer them.

**The harness owns the clock.** The game is pausable, so the loop pauses it,
reads state, decides, applies actions, then runs the game forward a fixed number
of in-game days and pauses again. The agent is never racing the game; from its
point of view HOI4 is turn-based. A critical event cuts the wait short, so a war
declaration is not sat on for six in-game days.

**Most moments are not decisions.** A deterministic reflex layer handles the
obvious and the urgent for free — empty construction queue, idle factories, pause
when something explodes. The model is woken only at decision points: a focus
finished, a research slot opened, a war started, a front broke, or the scheduled
review came round. That rule lives in one file, [`agent/policy.py`](src/hoi4_harness/agent/policy.py),
because it is the entire cost model of the project.

Everything else follows from those two:

| Lever | What it does |
|---|---|
| Delta briefs | One full situation report, then diffs until the diff gets big or N turns pass. A delta is a quarter the size of a brief. |
| Cached prefix | System prompt and tool schemas are byte-identical every call, so they bill at cache-read rates. Volatile text lives in the user message, never in `system`. |
| Fresh context each turn | Memory is a bounded journal the agent writes itself, plus a rolling digest — not a growing transcript. Old briefs contradict new ones and cost money to re-read. |
| 16 coarse actions | Not 200 fine ones. The tool block is on every request, and a long catalog confuses small models. |
| Hard budget ceilings | Calls, tokens and dollars. On exhaustion the loop drops to the reflex layer and keeps playing rather than stopping. |

Order-of-magnitude estimate for 1936→1939 at weekly reviews: ~190 wakes,
~2.2k input tokens each (≈90% cached after the first), ~200 output. That is
roughly 400k input / 40k output tokens for the campaign — a few dollars on a
frontier model, cents on a mid-tier one, free on a local one. The arithmetic and
its assumptions are in [docs/cost-and-timing.md](docs/cost-and-timing.md).

## Total customization

Everything the harness decides is a setting: precedence is defaults → environment
→ profile file → CLI flags. `hoi4-harness prompt` prints the exact system prompt a
run would use; `hoi4-harness doctor` prints the resolved configuration. After a
run, `hoi4-harness replay run.jsonl --turn 3` rebuilds the prompt that turn was
given and shows what another model calls in the same situation — nothing is
executed.

The layer worth knowing about is **guidance** — how much the harness tells the
model about playing HOI4. The mechanics half of the prompt is fixed (without it
the model cannot operate the tools); the strategy half is a swappable markdown
file:

| Pack | What it says |
|---|---|
| `none` | Nothing. Form your own view of the game. |
| `unconstrained` | Play to win by any in-game means: ahistorical, opportunistic, exploit-friendly, not scored on restraint. |
| `minimal` | Three mechanics facts, no strategy. |
| `doctrine` | Opinionated general advice. Default. |
| `historical` | Stay close to what the country actually did. |
| `coach` | A prescriptive checklist, for smaller models. |

```bash
hoi4-harness play --guidance none                     # benchmark the model, not my doctrine
hoi4-harness play --guidance ./my-doctrine.md         # or your own file
hoi4-harness play --system-prompt ./whole-thing.txt   # or replace the prompt entirely
hoi4-harness play --profile profiles/unleashed.toml --live   # off the leash
```

Also configurable: which actions exist at all (`--actions`, `--without`), whether
the reflex layer runs (`--no-reflex` hands every decision to the model), whether
the model is woken at all (`--no-llm` plays on reflexes alone — the baseline
every score is reported against), whether irreversible actions are gated and who
answers for them (`--allow-all`, `--confirm`), the wake rules, the pacing, the
budget ceilings, and the game
start. Three worked profiles ship in [profiles/](profiles/). Full list:
[docs/customization.md](docs/customization.md) and
[docs/guidance.md](docs/guidance.md).

The agent's in-game conduct is not policed. The only structural limits are
schema validation (a malformed call is a bug, not a strategy), the confirmation
gate you can switch off, and the budget.

## Two layers of control

The game already ships a competent operational commander: it assigns divisions
to fronts, plugs holes, pulls reserves, and does it within a tick at full map
resolution. A model doing that job is slower, dearer and worse. So the harness
supports a **hybrid** mode where the native AI runs operations and the model
commands the layer above it — who to invade, what to build toward, which theater
matters, what posture to hold:

```bash
HOI4_OPERATIONAL_CONTROL=ai hoi4-harness play --guidance hybrid --live --allow-all
```

Setting that mode removes the direct-command actions from the tool list (even if
a whitelist named them), adds `set_ai_directive` / `set_ai_posture` /
`delegate_army_to_ai`, and tells the model in the system prompt which layer it
is. Offering both vocabularies at once is the failure case — a direct order and a
standing directive that disagree produce behaviour attributable to neither.

Whether hybrid actually *plays better* is unanswered and issue-tracked: it needs
the same scenario run three ways (model-only, AI-only, hybrid) and scored the
same. See [docs/hybrid-control.md](docs/hybrid-control.md).

## The mod

[`mod/llm_bridge/`](mod/llm_bridge/) is a small HOI4 mod that gives the harness a
real bridge. The game prints a structured state line into `game.log` every tick
and the harness tails it — exact numbers, live, for the cost of a file read,
which is cheaper than parsing a save and cheaper than a vision call. It also
carries the decisions and `ai_strategy` blocks the hybrid layer pushes intent
through.

```bash
export HOI4_LOG_PATH="$HOME/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
hoi4-harness play --adapter logtail+input --live
```

The script tokens in it (variables, `on_action` names, `ai_strategy` types) are
the part that moves between game versions, and Paradox script fails silently — CI
checks structure, but only the game can tell you a token is real. Details and a
verification checklist: [docs/mod-bridge.md](docs/mod-bridge.md).

## How it fits together

```
 adapter  ->  observation  ->  policy  ->  agent loop  ->  env  ->  adapter
 (read)       (brief/delta)    (wake?)     (LLM+tools)    (validate,
                                                           gate, apply)
```

- **[adapters/](src/hoi4_harness/adapters/)** — the bridge to a running game. `mock` (complete),
  `logtail` (complete, needs the mod), `savegame` (parser done, mapping stubbed),
  `screen` (capture done, vision call stubbed), `input_driver` (write side, stubbed),
  `composite` (pair a reader with a writer).
- **[actions/](src/hoi4_harness/actions/)** — the catalog, a dependency-free schema validator, and
  provider-neutral tool specs.
- **[observation/](src/hoi4_harness/observation/)** — full briefs, deltas, and the rule for choosing.
- **[agent/](src/hoi4_harness/agent/)** — policy, memory, budget, prompts, loop, and the provider layer.
- **[eval/](src/hoi4_harness/eval/)** — scenarios with checkable objectives, scored on outcome *and* cost, and
  reported against a recorded reflex-only baseline (`eval --no-llm --write-baseline`).

## Status

| Piece | State |
|---|---|
| Agent loop, budget, memory, policy | Working, tested |
| Action catalog + validation | Working, tested (16 actions) |
| Observation briefs + deltas | Working, tested |
| Mock adapter | Working, tested, deterministic |
| Log-tail adapter (reads the mod's telemetry) | Working, tested |
| Hybrid control vocabulary + mode switch | Working, tested; unproven as strategy |
| LLM Bridge mod | Skeleton; structure CI-checked, script tokens need verifying in-game |
| Eval runner + scenarios | Working, tested (2 scenarios) |
| Guidance packs + profiles | Working, tested (6 packs, 3 example profiles) |
| Anthropic / OpenAI-compatible providers | Written, not yet run against a live key |
| Gemini provider | Written; the SDK surface moves — check this first if it fails |
| Save-file adapter | Clausewitz parser works; state mapping is TODO |
| Screen adapter | Capture works; vision→state is TODO |
| Input driver | Hotkeys and clicks work; per-action UI scripts are TODO |

Nothing that is a stub pretends otherwise at runtime: an adapter that cannot
perform an action refuses it, and a field it cannot read renders as `unknown`
rather than `0`.

## Using a model

```bash
export HOI4_LLM_PROVIDER=anthropic   ANTHROPIC_API_KEY=...
export HOI4_LLM_PROVIDER=openai      OPENAI_API_KEY=...
export HOI4_LLM_PROVIDER=openai      HOI4_LLM_BASE_URL=http://localhost:11434/v1   # Ollama
export HOI4_LLM_PROVIDER=google      GOOGLE_API_KEY=...

hoi4-harness play --provider anthropic --model claude-opus-5 --turns 40 --max-usd 2
hoi4-harness eval economy_ramp --provider openai --model gpt-5
hoi4-harness doctor
```

See [.env.example](.env.example) for every environment variable, and
[docs/customization.md](docs/customization.md) for the full settings table.


## Where it goes next

Seven milestones, each a claim the repo cannot currently make, tracked as
[milestones](https://github.com/Xocas12/hoi4-agent-harness/milestones) with the
work broken out in [issue #24](https://github.com/Xocas12/hoi4-agent-harness/issues/24):

| | Claim |
|---|---|
| [v0.1.1](https://github.com/Xocas12/hoi4-agent-harness/milestone/6) | The harness survives an overnight run |
| [v0.2](https://github.com/Xocas12/hoi4-agent-harness/milestone/1) | The harness plays a real game, not a mock |
| [v0.2.5](https://github.com/Xocas12/hoi4-agent-harness/milestone/7) | The model plays alongside a person, in the same campaign |
| [v0.3](https://github.com/Xocas12/hoi4-agent-harness/milestone/2) | The model commands and the game's AI executes |
| [v0.4](https://github.com/Xocas12/hoi4-agent-harness/milestone/3) | The compression story survives a war |
| [v0.5](https://github.com/Xocas12/hoi4-agent-harness/milestone/4) | A score from this harness means something |
| [v0.6](https://github.com/Xocas12/hoi4-agent-harness/milestone/5) | The machinery is boring and trustworthy |

Two of those issues are the questions the project exists to answer: whether an
agent can play coherently for a decade of game time on a budget
([#14](https://github.com/Xocas12/hoi4-agent-harness/issues/14)), and whether
splitting command between a model and the game's own AI beats either alone
([#10](https://github.com/Xocas12/hoi4-agent-harness/issues/10)). Everything else
is plumbing in service of asking them properly.

## License

MIT.
