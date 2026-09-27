# Playsets: custom maps and total conversions

Everything the harness knew about HOI4 used to be vanilla 1936. A total
conversion (Kaiserreich, Millennium Dawn, The New Order, Old World Blues) or a
map mod changes the countries, states, focus trees, technologies and calendar,
and a model that remembers the base game invents ids that do not exist there.
It does that in vanilla too: one live run tried `SWE_industrialization`,
`SWE_industrialization_effort`, `SWE_expand_industry` and
`SWE_defense_industrialization`, and nothing told it none of them were real.

## Pointing the harness at a playset

```bash
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_MOD_DIRS="C:/.../workshop/content/394360/1521695605;C:/.../mod/llm_bridge"
hoi4-harness index                    # what exists: countries, trees, techs, states
hoi4-harness index --country GER --all
```

`HOI4_GAME_DIR` is the **install** (the folder holding `common/`), not the
Documents folder where saves and logs live. `HOI4_MOD_DIRS` lists mod folders in
load order, separated like `PATH` (`;` on Windows, `:` elsewhere); `--game-dir`
and a repeated `--mod` do the same on the command line, and `game_dir` /
`mod_dirs` in a profile.

The index layers files the way the game does: a mod file at the same relative
path replaces the one before it, a `replace_path` in a mod's `descriptor.mod`
drops everything loaded earlier under that folder, and a localisation key is
renamed only from a `replace/` folder. The bridge mod itself does not count as
content: vanilla plus LLM Bridge is still `vanilla`.

## What it changes

With a playset configured:

- **Invented ids are refused locally**, before they reach the game where they
  would silently do nothing. `set_national_focus`, `start_research`,
  `queue_construction` (state by id or name), and the country in
  `set_ai_directive`, `diplomacy` and `set_trade` are checked, and a rejection
  names the nearest real ids:

  ```
  There is no focus 'SWE_expand_industries' in this playset (vanilla).
  Nearest real ones: SWE_expand_industry.
  ```

  (From the test fixture; its ids are invented, not the game's.)

  A real focus from another country's tree is refused as such, not as unknown.
- **The brief offers the relevant slice**: when no focus is running, the
  focuses the country can start *now* (prerequisites met, not yet done), capped
  at twelve. Never the whole tree, and never the technology list, which is far
  too long for a prompt. An adapter that cannot see completed focuses gets the
  tree's roots, labelled as such.
- **The transcript records the playset** — its name, the mods and versions, and
  a fingerprint of the indexed vocabulary — as its first record, so a transcript
  always says which world produced it.
- **A non-vanilla playset gets one paragraph in the system prompt** telling the
  model its memory of the base game and any history the guidance pack assumes
  may be wrong, and to trust the reports. It sits in the stable prefix, so it is
  cached like the rest.
- **Scenarios declare their playset** (`Scenario.playset`, `vanilla` for all six
  shipped ones) and a run configured for a different one is refused rather than
  scored against objectives written for another map.

Without a playset, nothing changes: ids pass through to the adapter as before,
and the mock accepts whatever it is given.

## Running the bridge mod alongside another mod

LLM Bridge only adds files, every one prefixed `llm_bridge`, in its own decision
categories, and it touches no vanilla file. It coexists with a total conversion
as long as it loads **after** it:

1. In the launcher's playset, put **LLM Bridge last**.
2. Why last: a total conversion commonly uses `replace_path` on folders like
   `common/decisions`, `common/on_actions` or `common/ai_strategy`, which drops
   files loaded *before* it in those folders — including the bridge's, if the
   bridge loads first. Loaded after, the bridge's files are added on top.
3. If the conversion replaces the country tag list, regenerate the directive
   targets for its tags so `set_ai_directive` can aim at them:

   ```bash
   hoi4-harness index --game-dir ... --mod ...      # read off the country list
   hoi4-harness mod-directives --tags GER,FRA,NFA,...
   ```

4. The bridge's `ai_strategy` types are vanilla's. A conversion that renames or
   removes strategy types leaves those directives inert; the directive-effect
   line in the brief is how you would notice (see
   [hybrid-control.md](hybrid-control.md)).

Unverified against a live total conversion: everything above is checked
against a fixture playset in `tests/fixtures/playset/`, built to exercise the
override rules, not against a real mod. Pointing it at one and reporting what
`hoi4-harness index` prints is the first thing to do with a real install.
