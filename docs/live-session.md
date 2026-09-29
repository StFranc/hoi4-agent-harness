# The first live session

Everything that can be built without the game has been. What is left needs one
sitting with a real install, in this order: each step either confirms a piece
works or produces the output the next piece of code is written from. Paste each
step's output into the issue named beside it.

You need: HOI4 installed, a non-ironman game you can save, and this repo
installed with the input extra.

```bash
pip install -e ".[dev,input]"
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
export HOI4_SAVE_DIR="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/save games"
```

(Use the plain `Documents` path if yours is not redirected into OneDrive; `doctor`
reports what it found.)

## 1. Before launching: the install's files (#1, #2, #7, #53)

```bash
hoi4-harness verify-bridge --game-dir "$HOI4_GAME_DIR"
hoi4-harness index --game-dir "$HOI4_GAME_DIR" --country SWE
```

- **on_actions MISSING** → those hooks never fire; the name has moved (#1).
- **completion on_actions** → the candidates for focus, research and
  construction completion events (#2). Paste the list; the hooks get written
  from it.
- **ai_strategy types the game never uses** → suspect directive types (#7). If
  any of `conquer`, `invade`, `protect`, `contain`, `befriend`, `antagonize`,
  `ignore` is listed, check `common/ai_strategy` for the current name.
- **index** → real countries and Sweden's actual focus tree (#53).

## 2. Install the mod, load it last

Copy `mod/llm_bridge/` and `mod/llm_bridge.mod` into your `mod/` folder, enable
**LLM Bridge** in the launcher, and put it **last** in the playset
([playsets.md](playsets.md) says why). Start a 1936 game as any country.

Check the **decisions** tab: the *LLM Bridge* categories should show readable
names ("Directive: hold the line", "Target: GER"). Raw keys like
`llmb_posture_defensive` mean the localisation did not load — the BOM question
in #59; say so in #1.

## 3. Telemetry (#1)

Unpause for a few days, take the *Emit telemetry now* decision once, pause.

```bash
hoi4-harness verify-bridge --log "$HOI4_LOG_PATH"
hoi4-harness observe --adapter logtail
```

- Every field under **expanded** is real; anything under **LITERAL** is a token
  that did not expand and needs its name fixed.
- The brief from `observe` should have numbers for political power, stability,
  war support, factories and manpower. #1's acceptance is that brief plus the
  `verify-bridge` output.

## 4. Directives through the mod (#7, #8)

In the decisions tab: *Target: POL*, then *Directive: protect*. Then *Directive:
hold the line*. Then:

```bash
hoi4-harness verify-bridge --log "$HOI4_LOG_PATH"   # events: directive_raised, posture_defensive
hoi4-harness observe --adapter logtail               # AI control: posture defensive | directives: protect POL
```

If both show up, directives and posture are verifiable through the log. Take
*Directive: stand down* and check both disappear.

## 5. A save (#6)

Save the game (not ironman), then:

```bash
hoi4-harness save-inspect
```

Paste the output into #6. It lists the save's top-level blocks and the player
country's own keys with sample values; `_to_state` gets written from that, one
field at a time.

## 6. Clicking (#3, #8)

```bash
cp profiles/ui_scripts.example.json runs/ui_scripts.json
```

Walk each script in the file by hand in the game and fix every step that does
not match what you see (the template is guesses). Then record the click targets
the scripts name:

```bash
hoi4-harness calibrate
hoi4-harness check-scripts     # every placeholder valid, every concrete target calibrated
```

Try one action at a time, dry run first (it logs the path and sends nothing),
then live:

```bash
hoi4-harness play --adapter logtail+input --actions start_research,note,advance_time --turns 1
hoi4-harness play --adapter logtail+input --actions start_research,note,advance_time --turns 1 --live
```

What comes back per action is **verified**, **rejected** (the game did not
change — the script is wrong somewhere), or **unverified** (the reader cannot
see the proving field). Through the log reader, focus, research, construction
and production are unverified until the mod emits them or the save mapping
reads them — expected at this stage; the result is still the evidence that the
click path works, so note what you saw happen in-game. Paste a transcript
excerpt into #3; delegation's result into #8.

## 7. With API keys (#22, #23)

```bash
HOI4_LIVE_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/live -v -rs

# Set all four rates from the provider's price sheet first:
# HOI4_USD_PER_M_INPUT, _OUTPUT, _CACHED_INPUT, _CACHE_WRITE
hoi4-harness play --provider anthropic --turns 20 --run-dir runs/priced
```

Paste the conformance output into #22. Compare the run's `spend.usd` with the
provider's usage page for the same window and paste both numbers into #23.

## 8. The measurements (#10, #14)

With a real model configured and `tiktoken` installed:

```bash
pip install tiktoken
hoi4-harness measure --campaign wartime_1939_1941 --provider anthropic
hoi4-harness experiment winter_crisis --seeds 5 --provider anthropic
hoi4-harness experiment defensive_war --seeds 5 --provider anthropic
```

`measure` now counts tokens with a real tokenizer and reports billed tokens and
cache hits; the table in [cost-and-timing.md](cost-and-timing.md) gets
replaced with it (#14). The experiment tables go into
[hybrid-control.md](hybrid-control.md#the-experiment) with whatever they show,
including "no difference" (#10). Against the mock these measure the harness;
against the live game (`--adapter logtail+input`, once steps 3-6 hold) they
measure the thing the project exists to ask.
