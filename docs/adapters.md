# Adapters

An adapter is a bridge to one running game. Each sees a different subset of the
world, so the contract makes that subset explicit rather than papering over it.

```python
class GameAdapter:
    supported_actions: frozenset[str]
    def info(self) -> AdapterInfo: ...
    def read_state(self) -> GameState: ...
    def apply(self, call: ActionCall) -> ActionResult: ...
    def pause(self) -> None: ...
    def resume(self, speed: int = 3) -> None: ...
    def advance(self, days: int) -> GameState: ...   # returns early on a critical event
```

Two invariants hold everywhere:

- **Refuse, don't fake.** An action outside `supported_actions` returns
  `error_kind="unsupported"` with the list of what *is* supported. The model can
  work with that; it cannot work with a silent no-op reported as success.
- **Unknown, not zero.** A field the adapter could not read goes in
  `unknown_fields` and renders as `unknown`. A fabricated `0` for war support is
  worse than a gap, because the agent will reason confidently from it.

## mock — complete

Seeded, deterministic, no HOI4 install. A clock, resources that accrue, queues
that drain, scripted historical events (including a critical one on 1939-09-01).
Every test runs against it, and `hoi4-harness play` uses it by default, so the
whole harness is exercisable for free.

Not a simulator. It is shaped like the game, not accurate to it — use it to test
the loop, never to draw conclusions about strategy.

## logtail — complete, needs the mod

Tails `game.log` and rebuilds `GameState` from the telemetry the
[LLM Bridge mod](mod-bridge.md) emits. Exact numbers, live, at the cost of a file
read — the best read path in the repo, and the only one cheap enough to poll
often.

Read-only, so compose it: `--adapter logtail+input`. It refuses to guess: a
version mismatch raises, an unexpanded script token is marked unknown rather
than read as zero, and fields the mod does not emit yet are declared unknown up
front.

### Finding the game

Both the log-tail and savegame adapters locate the game's user directory through
`hoi4_harness.paths`, which searches OneDrive-redirected Documents as well as the
plain one. That is not hypothetical: on the machine this was checked against, the
only copy lived under `OneDrive\Documents` and every previous default missed it.

A path you supply explicitly is used or it fails — discovery never runs as a
fallback, because searching on after you named a directory would mean reading a
different campaign than the one you pointed at.

## savegame — parser and targeted reading done, field mapping needs a real save

Reads the most recent `*.hoi4` autosave. `parse_clausewitz()` handles Paradox's
`key=value` / `key={...}` text format including repeated keys.

A save is 68–96 MB on the machine this was checked on, so the adapter no longer
tokenizes all of it. A text save writes its top level at column 0, so
`top_level_spans()` finds each top-level entry without parsing, `extract()`
parses only the entries asked for, and `country_block()` cuts out the player's
own block. On a synthetic 53 MB save that is 0.6 s against 11.8 s for a full
parse. When the layout does not show a key (a save on one line), it falls back
to the full parse rather than reporting the key absent.

What is left is the mapping from the player's block to `GameState`, which has to
be verified against a real save rather than guessed — block names have moved
between patches. So the adapter reads the date and the player and puts the
player's parsed block in `state.raw["country"]`, and declares everything else
unknown until each field is checked. To do that work from the file rather than
from memory:

```bash
hoi4-harness save-inspect                   # newest save in HOI4_SAVE_DIR
hoi4-harness save-inspect path/to/save.hoi4
```

prints the top-level entries by size and the player country's keys with a sample
of each value — the input for writing `_to_state`, one verified field at a time.

Constraints: non-ironman text saves only (ironman is binary and compressed); a
save is a snapshot, so pair it with a short autosave interval; read-only, so
compose it with a writer.

## screen — capture done, vision TODO

Grabs the window or a named region with `mss`. `REGIONS` names the parts of the UI
worth cropping (top bar, alert cluster, outliner) in 1920x1080 reference
coordinates.

`read_state()` needs a vision model call: send the PNG through the same
provider-neutral LLM layer with a schema matching `GameState`, and put anything
the model will not commit to in `unknown_fields`. This is the only adapter that
works on ironman and the only one that sees alerts and the map — and the most
expensive per observation, so it is for what numbers cannot express, not for
routine ticks.

## input_driver — mechanics and verification done, click paths recorded per install

The write side. Hotkeys (`HOTKEYS`) are preferred over coordinates because they
survive resolution and UI-scale changes; coordinates live in a calibration dict,
not in code — `hoi4-harness calibrate` writes it (with the screen resolution it
was captured at), and loading a calibration from another resolution is refused
rather than applied. `dry_run=True` logs intended input without sending it, which is how
the tests and the default CLI run.

Input is gated on window focus. Before anything is sent, the adapter checks that
the focused window's title contains `window_title` -- a case-insensitive
substring, because the real title carries a suffix (`Hearts of Iron IV (OpenGL)`)
and an exact match would never fire. If the game is not focused, the action comes
back as a failed `ActionResult` with the name of the window that *is* focused,
which the agent can read and act on like any other rejection.

A platform with no implemented check reports **unsupported** and refuses, rather
than passing. A guard that always returns true is worse than no guard, because it
gets trusted. `--no-window-guard` is the deliberate opt-out.

### UI scripts: act, then prove it

A blind click is unverifiable, and an unverified action reported as success is
the worst failure mode in this whole design. So a scripted action has two halves,
kept apart on purpose ([`ui_scripts.py`](../src/hoi4_harness/adapters/ui_scripts.py)):

- **The click path** is data. It depends on the game version, the UI scale and
  your layout, so — like coordinates — it lives in a file you record against
  your own game, `<run-dir>/ui_scripts.json` (or `HOI4_UI_SCRIPTS`). Steps are
  `press`, `click` (a calibrated target), `type` and `wait`, and may carry
  `{placeholders}` filled from the action's arguments:

  ```json
  {"schema": 1, "scripts": {"set_national_focus": [
    {"press": "f"}, {"click": "focus.search"}, {"type": "{focus_id}"},
    {"click": "focus.first_result"}, {"press": "escape"}]}}
  ```

- **The verification** is code, because it does not depend on the UI: a started
  focus is running, a researched technology is in a slot, a queued building
  lengthens the queue by `count`, a production line has the factories asked for,
  and a hired advisor costs political power (weak, since no adapter reports
  advisors, and said so). The hybrid actions verify the same way: an army is (or
  is no longer) under AI control, the posture — global or on a theater — is the
  one asked for, a directive stands for exactly the country named, and
  standing down leaves no directive and no posture.

After the steps, the driver re-reads the game through the reader it is composed
with and polls until the verification passes or a timeout expires. The result is
one of:

| Outcome | When |
|---|---|
| ok, "done and verified" | the consequence showed up |
| `rejected`, "input sent but the game did not change: …" | it did not — the UI was not where the script expected |
| `unverified` | the reader cannot see the field that would prove it (the log-tail bridge does not emit the focus yet); treat it as not done |
| `not_executed` | dry run: the path is logged, nothing is sent |

A script is refused before any input is sent when a click target has no
calibrated coordinate, and a scripts file naming an action with no verification
(anything outside the five peacetime actions and the four hybrid ones) is
refused at load time.

Through the log-tail reader, posture is observable — the mod emits an event when
it changes, and the reader tracks it from the first one. So are standing
directives: each generated per-target branch logs its own literal line
(`kind=14|detail=protect POL`, no variable to expand), and the reader rebuilds
the list from those and resets it on stand-down. Directives raised before the
harness started reading are missing until the next stand-down; a raise it saw
never is. Delegated armies are not reported by the mod, so delegation comes
back `unverified` there — the honest answer until the mod emits it. An action
with no recorded script is refused, as before.

To set one up: copy [`profiles/ui_scripts.example.json`](../profiles/ui_scripts.example.json)
— a template whose paths are guesses at the UI, to be checked step by step — to
your run directory, fix it against what you see, then run `hoi4-harness
calibrate`, which now walks the targets your scripts click. `calibration.json`
in the run directory (or `HOI4_CALIBRATION`) is loaded automatically.

## composite

Pairs a reader with a writer: `--adapter logtail+input`, `savegame+input` or
`screen+input`.

`advance()` blocks on the **in-game** date rather than returning after a single
read, and returns early on a critical event. Without that, a real bridge takes a
turn every few milliseconds and burns a budget in seconds. A wall-clock deadline
is the backstop, so a wedged game ends the turn instead of hanging the run, and
the game is left paused on every exit path including the timeout and an exception
from the reader.

Who owns the clock is a setting. With `clock_owner="harness"` (the default) the
composite unpauses, waits, and pauses again. With `clock_owner="player"` it never
sends a clock instruction at all — it only watches the date move — and
`set_game_speed` is removed from the action list so the model cannot take the
clock either. That is the mode to use when a person is playing the same
campaign.
