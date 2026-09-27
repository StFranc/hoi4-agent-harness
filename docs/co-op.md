# Co-op: playing alongside the model

The harness was designed as if the agent were the only player: it owns the clock
and the keyboard. With a person in the same campaign, both assumptions break,
and three settings fix them.

| Mode | Flag | Who acts |
|---|---|---|
| Advisor | `play --advisor` | You. The model recommends through real tool calls; nothing executes. Needs only a read-only adapter. |
| Player clock | `--player-clock` | Either, but the harness never pauses, resumes or sets speed. |
| Handover | `play --handover` | The model decides whenever it likes; its actions wait in a queue until you hand it the keyboard. |

## Handover

Two people cannot share a keyboard, so handover is turn-taking:

```bash
hoi4-harness play --adapter logtail+input --live --player-clock --handover --run-dir runs/coop
# ...when there is a lull:
hoi4-harness handover --run-dir runs/coop            # shows the queue, then grants control
hoi4-harness handover --run-dir runs/coop --release  # take it back mid-run
```

- **Every action is queued, not executed**, and the tool result says so — the
  model is told not to plan as if it had happened. `note` and `advance_time`
  change nothing you could be clicking on and pass straight through.
- **What is queued is always visible**: `handover-queue.json` in the run
  directory, rewritten on every change, and a line in each brief.
- **The grant is a file** (`handover.grant` in the run directory). `hoi4-harness
  handover` creates it; bind that command to a hotkey with whatever your OS
  offers. The harness runs the queue at the end of its next turn and deletes the
  file when done. Deleting it yourself takes the keyboard back: the harness stops
  *between* actions and keeps the rest queued.
- **Each action is re-checked before it runs.** The world moved while it waited,
  so a focus queued last week is reported stale if one is already running, a
  research call if no slot is free, a production change if the factories are no
  longer there. Stale actions are reported, never clicked. Everything is also
  validated again, and the adapter's own verification still applies.
- **The next brief says what the window actually did** — ran, stale, failed,
  aborted — once, so the model replans on what happened.
- **The transcript records every window**: when it was granted, what ran, and
  when each action was queued and when it ran. A model whose plans routinely go
  stale before they run is telling you the handover cadence is wrong.

A file rather than a keyboard hook, because a hook in this process listening for
your keys would itself be input contention, and a file is the one signal every
platform, terminal and hotkey tool can produce.

## Split portfolio

Divide by domain rather than by time: the model takes the economy, you keep the
army, navy and diplomacy. The action whitelist already expresses it:

```bash
hoi4-harness play --adapter logtail+input --live --player-clock --handover \
  --actions set_production,queue_construction,start_research,set_national_focus,hire_advisor,note,advance_time
```

## Not for scoring

Once a person acts too, a score says nothing about the model. The eval runner
refuses a run configured for handover or advisor mode rather than producing a
number that means nothing.

Handover needs an input driver that can act (#3) to do anything in a real game;
against the mock it is fully exercised by `tests/test_handover.py`.
