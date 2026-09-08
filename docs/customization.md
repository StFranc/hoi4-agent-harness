# Customization

Everything the harness decides is a setting, and every setting has the same
precedence: **defaults → environment → profile file → CLI flags**.

```bash
hoi4-harness play --profile profiles/unleashed.toml --turns 200 --live
```

`hoi4-harness prompt` prints the exact system prompt a run would use, and
`hoi4-harness doctor` prints the resolved configuration. Nothing is hidden from
you at run time.

## What you can change

### The model

| Setting | Flag / env | Notes |
|---|---|---|
| Provider | `--provider`, `HOI4_LLM_PROVIDER` | `anthropic`, `openai`, `google`, `scripted` |
| Model | `--model`, `HOI4_LLM_MODEL` | any string the provider accepts |
| Endpoint | `--base-url`, `HOI4_LLM_BASE_URL` | Ollama, vLLM, LM Studio, OpenRouter, ... |
| Reasoning effort | `--effort`, `HOI4_LLM_EFFORT` | where supported |
| Max tokens, temperature | `HOI4_LLM_MAX_TOKENS`, `HOI4_LLM_TEMPERATURE` | |
| Triage model | `HOI4_TRIAGE_*` | the cheap second role |

### What the model is told

| Setting | Flag | Notes |
|---|---|---|
| Guidance pack | `--guidance` | built-in name or a path — see [guidance.md](guidance.md) |
| Whole system prompt | `--system-prompt` | replaces mechanics too; you are on your own |
| Appended text | `HOI4_SYSTEM_PROMPT_EXTRA` | added after the guidance |
| Standing objective | `--objective` | repeated every turn |

### What the model may do

| Setting | Flag | Notes |
|---|---|---|
| Allowed actions | `--actions a,b,c` | whitelist; narrows the tool block too |
| Forbidden actions | `--without a,b` | blacklist |
| Confirmation gate | `--allow-all` | let irreversible actions through |
| Live mode | `--live` | dry run is the default |
| Actions per turn | `max_actions_per_turn` | |
| Tool rounds per turn | `max_tool_rounds_per_turn` | how many times it can react to a rejection |
| Error tolerance | `max_consecutive_llm_errors`, `HOI4_MAX_CONSECUTIVE_LLM_ERRORS` | consecutive provider failures before the run stops; a fatal error stops immediately regardless |

### Pacing and cost

| Setting | Notes |
|---|---|
| `days_per_turn` | in-game days between scheduled reviews |
| `wake_on_no_focus`, `wake_on_free_research_slot` | opportunity wakes; turn off for a strictly scheduled run |
| `planner_enabled` | `--no-llm` never wakes the model at all: reflexes only, zero model calls. `eval --no-llm --write-baseline` records those scores as the baseline every score card is reported against |
| `reflex_enabled` | `--no-reflex` makes the model decide everything, including the boring parts |
| `full_brief_every` | turns between full briefs; the rest are deltas |
| `budget.max_usd`, `max_llm_calls`, `max_input_tokens`, `max_output_tokens` | hard ceilings; on exhaustion the run drops to reflex rather than stopping |
| `budget.usd_per_m_input` / `usd_per_m_output` | your price sheet, so the run reports real money |

### The game

| Setting | Notes |
|---|---|
| `adapter` | `mock`, `logtail`, `savegame`, `screen`, and `X+input` pairs |
| `operational_control` | `llm` (model commands directly) or `ai` (native AI runs operations — see [hybrid-control.md](hybrid-control.md)) |
| `clock_owner`, `--player-clock` | `harness` (pauses and drives the clock) or `player` (never touches it — required when a person is playing the same campaign) |
| `country`, `start_date`, `seed` | mock adapter start |
| `log_path` / `HOI4_LOG_PATH` | `game.log`, for the logtail adapter |
| `save_dir`, `window_title` | real-game adapters |
| `enforce_window_focus`, `--no-window-guard` | refuse to send input unless the game is focused (default on) |

## Profiles

A profile is one JSON or TOML file holding any subset of the above. Nested
`[planner]`, `[triage]` and `[budget]` tables merge rather than replace, and an
unknown key is an error rather than a silent no-op.

Three worked examples ship in [`profiles/`](../profiles/):

- **[unleashed.toml](../profiles/unleashed.toml)** — no coaching, no reflex layer,
  no confirmation gate, frontier model, $5 ceiling.
- **[cheap-local.toml](../profiles/cheap-local.toml)** — a 14B model on Ollama with
  heavy coaching and six actions.
- **[historical-run.json](../profiles/historical-run.json)** — historical guidance,
  a 180-turn Sweden run with real per-token prices set.

## Extending rather than configuring

Some things are code, not settings, and are meant to be:

- a **new action** → an `ActionSpec` in `actions/catalog.py` plus adapter support;
- a **new provider** → implement `LLMClient.complete` (~120 lines);
- a **new adapter** → subclass `GameAdapter`;
- a **new scenario** → a `Scenario` with predicates over `GameState`;
- a **different wake rule** → `agent/policy.py`, deliberately one small function.
