# Hybrid control: the game's AI runs operations, the model runs strategy

## The premise

HOI4 ships a competent operational commander. It assigns divisions to fronts,
plugs holes, pulls reserves, retreats when a pocket is forming, and does all of
it within a tick, for free, at full map resolution.

A language model doing that same job is slower, more expensive, and worse. It
sees a text summary of a map, it answers in tens of seconds, and by the time it
has decided which division to move the encirclement has closed. Asking it to
micromanage fronts is asking it to lose at the one thing the built-in AI is
already good at.

What a model is good at is the layer above — the decisions that take weeks, have
no local answer, and are exactly what the native AI is worst at.

## The split

| Layer | Owner | Examples |
|---|---|---|
| Grand strategy | LLM | who to invade and when, whom to guarantee, which faction to join, when to sue for peace |
| Force design | LLM | doctrine, research direction, division templates, the industry-to-army ratio |
| Priorities | LLM | which theater matters this quarter, what to build, what posture to hold |
| Operations | game AI | front assignment, movement, reinforcement, local reserves |
| Tactics | game AI | when to attack a province, when to fall back, garrisoning |

## The seam

Four actions, and a mod that turns them into something the vanilla AI already
understands:

```
delegate_army_to_ai {army, delegate}          hand an army over, or take it back
set_ai_posture      {posture, theater?}       defensive | balanced | offensive
set_ai_directive    {directive, target, weight}  invade | protect | contain | befriend | antagonize | ignore
clear_ai_directives {}                        stand everything down
```

Underneath, the harness raises a country flag; an `ai_strategy` block in
[the mod](../mod/llm_bridge/common/ai_strategy/) keys on that flag and starts
biasing the AI's own scoring. No engine hooks, no fighting the AI for control of
a unit — the model sets intent and the AI executes it with its own machinery.

### Targets

An `ai_strategy` block names its target as a literal (`id = "POL"`), and no
variable-driven `id` is known to work there. So the mod carries one block per
(directive, target), each gated on its own flag (`llmb_protect_POL`), generated
from a template by [`modgen.py`](../src/hoi4_harness/modgen.py) into
`llm_bridge_targets.txt`. Raising a directive is two stable clicks in the
**LLM Bridge: targets** decision category:

1. `Target: POL` — makes POL the selected target (and clears the previous one);
2. `Directive: protect` — sets `llmb_protect_POL` for the selected target.

That is targets + directives decisions, not their product, and the harness side
(`set_ai_directive` in the catalog) does not change. Directing `protect FIN` and
then `protect POL` raises two distinct blocks aimed at the right countries;
`stand down` clears every one.

The default tag list covers the countries a vanilla 1936 campaign most plausibly
names. For a playset with other tags:

```bash
hoi4-harness mod-directives --tags GER,ENG,FRA,KAI,...   # regenerate
hoi4-harness mod-directives --check                        # CI: are the files current?
```

Two things only the game can confirm, tracked in #1: that the `ai_strategy`
types in `modgen.STRATEGIES` (`conquer`, `invade`, `protect`, `contain`,
`befriend`, `antagonize`, `ignore`) are real in your version, and what `value`
moves behaviour. The directive's `weight` is not transmitted — each block has a
fixed value. A wrong type is ignored silently by the game, which is what the
directive-effect line below exists to catch.

### Did the directive do anything?

The adapter's answer to `set_ai_directive` only proves the harness recorded it.
So the environment snapshots, when a directive is raised, what an observer could
see change if the AI took it seriously — divisions on fronts facing the target,
whether this country is at war with it, whether it fights on this side — and
every brief after that, full or delta, carries one line per standing directive
diffed against that snapshot:

```
Directive effect (observed, not intended):
  invade POL, raised 14d ago: divisions facing POL 4 -> 16 on 1 front(s)
  invade DEN, raised 21d ago: NO OBSERVABLE CHANGE
```

Re-weighting a directive keeps its original baseline, so re-issuing one that
does nothing cannot reset its clock. This is also what catches #7's silent
failure: a directive aimed at the wrong country reads NO OBSERVABLE CHANGE
instead of looking identical to one that works.

### Theaters

A theater is a **front, by name** — the cheapest definition that can express the
most common decision a player makes, "hold in the east, press in the west", and
one the brief already prints. `set_ai_posture` with a `theater` overrides the
global posture on that front only; without one it sets the posture for every
front that has no posture of its own. An unknown front name is rejected with the
list of real ones. The brief shows both layers
(`AI control: posture defensive | theaters: west offensive`) and the delta
reports a theater changing posture on its own line. `clear_ai_directives` drops
theater postures along with everything else.

On the mod side this is still global: the posture flags in
`llm_bridge_directives.txt` have no notion of a front. Scoping them needs a front
identity the game script can key on (a strategic region or a state the front
runs through), and choosing that is a live-game job, like the rest of #1.

## Turning it on

```bash
HOI4_OPERATIONAL_CONTROL=ai hoi4-harness play --guidance hybrid --live --allow-all
```

Setting `operational_control` changes three things at once, and deliberately so:

1. The direct-command actions (`set_army_order`, `set_air_mission`,
   `set_naval_mission`) disappear from the tool list, even if an explicit
   whitelist named them.
2. The directive actions appear.
3. The system prompt gains a paragraph saying which layer the model is.

Offering both vocabularies at the same time is the failure case: the model gives
an army a direct order *and* a standing directive, they disagree, and the
resulting behaviour is attributable to neither.

## What is actually built

Working and tested: the action vocabulary, the control-mode switch, the prompt
layer, the `hybrid` guidance pack, state rendering for delegation and standing
directives, and mock-adapter support so the whole path runs offline.

Built but unverified in a live game: per-target directive blocks (see
[Targets](#targets)), and verified input-driver scripts for all four hybrid
actions (delegation, posture, directive, stand down) whose click paths still
have to be recorded against a real game ([adapters.md](adapters.md#ui-scripts-act-then-prove-it)).
Not built: the mod reporting delegated armies, so through the log reader
delegation stays unverified (standing directives are reported, as literal log
lines from the generated branches); and
theater-scoped posture inside the mod (the harness side and the mock are built;
see [Theaters](#theaters)).

Not answered: **whether hybrid actually plays better.** That is the interesting
question and it needs three runs of the same scenario — model-only, AI-only,
hybrid — scored the same way. Until that exists this is a hypothesis with an
implementation, and the repo should say so.

### The experiment

The runner exists; the result does not yet.

```bash
hoi4-harness experiment defensive_war --seeds 5 --provider anthropic --model ...
```

runs the scenario at the same seeds three ways and prints median, range and
spend per arm, then a verdict line that says plainly when there is no
difference:

| Arm | Configuration |
|---|---|
| model-only | `operational_control=llm`, reflexes on, the configured guidance |
| AI-only | no model at all; the reflex layer delegates every army and holds a defensive posture while a front is losing ground (`reflex_delegate`, off everywhere else) |
| hybrid | `operational_control=ai`, `--guidance hybrid`; delegating is the model's call |

The AI-only arm is the one that matters: if it scores close to hybrid, the model
is decorative on that scenario. Against the mock with the scripted planner all
three arms score 0.00 on `defensive_war` — which measures the mock's crude front
rule and a planner that never delegates, nothing more. A result worth writing
here needs a real game (#7, #8), a real model, and at least five seeds.

## Why this is the more promising direction

Two reasons beyond raw skill.

**Cost.** Operations are the part of the game that generates constant, urgent,
low-value decisions. Handing them to the AI removes the traffic that would
otherwise force the wake rule wide open during wartime — which is precisely when
the token bill would explode. See [cost-and-timing.md](cost-and-timing.md).

**Attribution.** When the model only makes strategic decisions, a run's outcome
is evidence about strategic decision-making. When it is also moving divisions,
a loss could be either, and you have learned nothing.
