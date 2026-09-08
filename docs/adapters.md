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

## savegame — parser done, mapping TODO

Reads the most recent `*.hoi4` autosave. `parse_clausewitz()` handles Paradox's
`key=value` / `key={...}` text format including repeated keys.

What is left is the mapping from parsed blocks to `GameState`, which has to be
verified against a real save rather than guessed — block names have moved between
patches. The three TODOs are marked in `_to_state`.

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

## input_driver — mechanics done, per-action scripts TODO

The write side. Hotkeys (`HOTKEYS`) are preferred over coordinates because they
survive resolution and UI-scale changes; coordinates live in a calibration dict,
not in code. `dry_run=True` logs intended input without sending it, which is how
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

The open work is one UI script per action, each ending in a verify step: act,
re-read, confirm the state actually changed. A blind click is unverifiable, and
an unverified action reported as success is the worst failure mode in this whole
design.

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
