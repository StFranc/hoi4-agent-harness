# The action catalog

The catalog is the agent's entire vocabulary. It is small on purpose: 16 verbs,
listed by `hoi4-harness actions` (add `--json` for schemas).

| Action | Category | Notes |
|---|---|---|
| `set_national_focus` | politics | One at a time |
| `start_research` | research | Fails when no slot is free |
| `queue_construction` | economy | Enum of buildings |
| `set_production` | economy | Declarative: total factories on a line |
| `design_division_template` | army | Nested battalion list |
| `deploy_divisions` | army | Consumes equipment |
| `set_army_order` | army | Confirmation-gated |
| `set_air_mission` | air | |
| `set_naval_mission` | navy | |
| `hire_advisor` | politics | Costs political power |
| `enact_decision` | politics | |
| `diplomacy` | diplomacy | Confirmation-gated; includes `declare_war` |
| `set_trade` | economy | |
| `set_game_speed` | clock | The harness normally owns this |
| `advance_time` | clock | Ends the turn |
| `note` | memory | Free; writes to the journal |

## Design rules

**Few, coarse verbs.** One `queue_construction` with a building enum beats twelve
building-specific tools. Every tool is prompt tokens on every request and one more
thing to confuse a small model.

**Declarative, not gestural.** `set_production(equipment, factories)` says what
the world should look like. "Click the plus arrow four times" is the adapter's
problem, and it is the adapter that knows whether the click landed.

**Enums over free text** wherever the game has a fixed set, so a bad call is
rejected locally with a message naming what was allowed — cheap — instead of
costing a round trip.

**`note` is free.** Without somewhere to put reasoning that is not an action, a
model will smuggle it into arguments.

## Validation

`actions/validate.py` is a dependency-free validator covering exactly what the
catalog uses: type, required, enum, minimum/maximum, maxLength, array items,
nested objects, `additionalProperties: false`. Booleans are explicitly not
integers.

Error messages are the product here. Compare:

```
invalid input                                       # useless
queue_construction.building: 'space_elevator' is not one of
[civilian_factory, military_factory, dockyard, ...]  # recoverable
```

The second one gets fixed on the next tool round; the first burns a turn.

## Confirmation gate

Actions with `requires_confirmation=True` are irreversible or war-starting. In
dry-run mode (the default) they are refused with an explanation. With `--live`
they route through `env.confirm_hook`, which is a callable taking a
`confirm.Confirmation` (the call, the game date, the turn, and the model's own
rationale) and returning whether to proceed; with no hook set, they proceed.

`play --confirm` installs the one this repo ships: a terminal prompt showing the
action, its arguments and the rationale, answered `yes` / `no` / `always`. It is
off by default so unattended runs are never blocked, and with no input at all the
answer is no. Every decision, denial included, is written to the transcript as a
`confirmation` record — a run where a person blocked three invasions is not the
same run as one that never tried. Keep new irreversible actions on that list —
the model cannot undo a declaration of war, and neither can you.

## Adding an action

1. Add an `ActionSpec` to `actions/catalog.py`.
2. Implement it in the adapters that can perform it, and add the name to their
   `supported_actions`.

Adapters that cannot perform it need no change: they refuse it, the tool list they
advertise excludes it, and the agent never sees it.
