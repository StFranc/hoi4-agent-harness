"""What identifiers exist in this playset: focuses, technologies, states, tags.

``set_national_focus`` takes a free-text id and nothing in the harness knew which
ids are real. A live run produced ``SWE_industrialization``,
``SWE_industrialization_effort``, ``SWE_expand_industry`` and
``SWE_defense_industrialization`` across one campaign -- a model inventing
plausible ids because nothing told it otherwise. On a total conversion it cannot
even guess plausibly.

So the harness reads the vocabulary from the files the game itself loads: the
install, then each active mod in load order. A mod file at the same relative
path replaces the game's (or an earlier mod's), and a mod's ``replace_path``
drops everything loaded before it under that folder -- the same layering the
game applies. From that:

* an invented id is rejected locally, with the nearest real ones, instead of
  reaching the game and silently doing nothing;
* the brief can offer the *relevant slice* -- the focuses this country can take
  next, not the whole tree and never every technology;
* a transcript records which playset produced it.

The parser is the savegame adapter's Clausewitz tokenizer with comments
stripped. Script files use more syntax than it models (comparison operators,
inline math), but ids sit in plain ``key = value`` pairs, which is all this
reads.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.savegame import parse_clausewitz
from .types import ActionCall, ActionResult, GameState

VANILLA = "vanilla"

#: Arguments that name a country, per action.
TAG_ARGUMENTS = {
    "set_ai_directive": "target",
    "diplomacy": "target",
    "set_trade": "from_country",
}

#: Words the harness accepts in place of a real state, because its own reflexes
#: and the mock use them. A real adapter resolves them itself.
STATE_ALIASES = {"capital"}


# --- the playset -------------------------------------------------------------


@dataclass
class Mod:
    path: Path
    name: str
    version: str = ""
    replace_paths: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Mod:
        path = Path(path)
        descriptor = path / "descriptor.mod"
        name, version, replace = path.name, "", []
        if descriptor.is_file():
            text = descriptor.read_text(encoding="utf-8-sig", errors="replace")
            name = _descriptor_value(text, "name") or name
            version = _descriptor_value(text, "version") or ""
            replace = re.findall(r'^\s*replace_path\s*=\s*"([^"]+)"', text, flags=re.M)
        return cls(path=path, name=name, version=version,
                   replace_paths=[p.strip("/").replace("\\", "/") for p in replace])


def _descriptor_value(text: str, key: str) -> str | None:
    match = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, flags=re.M)
    return match.group(1) if match else None


@dataclass
class Playset:
    """The install plus the active mods, in load order (last wins)."""

    game_dir: Path
    mods: list[Mod] = field(default_factory=list)

    @classmethod
    def from_paths(cls, game_dir: Path, mod_dirs: Iterable[Path] = ()) -> Playset:
        game_dir = Path(game_dir)
        if not (game_dir / "common").is_dir():
            raise FileNotFoundError(
                f"{game_dir} does not look like a HOI4 install: no common/ folder. Point "
                "HOI4_GAME_DIR at the folder holding hoi4.exe, not the Documents folder."
            )
        mods = []
        for mod_dir in mod_dirs:
            if not Path(mod_dir).is_dir():
                raise FileNotFoundError(f"mod folder {mod_dir} does not exist")
            mods.append(Mod.load(Path(mod_dir)))
        return cls(game_dir=game_dir, mods=mods)

    @property
    def name(self) -> str:
        """``vanilla``, or the mods' names in load order."""
        # The bridge mod is plumbing, not content: a vanilla game with only the
        # bridge loaded is still vanilla to every scenario and guidance pack.
        content = [mod.name for mod in self.mods if not _is_bridge(mod)]
        return " + ".join(content) if content else VANILLA

    @property
    def is_vanilla(self) -> bool:
        return self.name == VANILLA

    def layered(self, folder: str, pattern: str = "*.txt") -> list[Path]:
        """Every file under ``folder`` the game would load, overrides applied."""
        files: dict[str, Path] = {}
        for root, replace in [(self.game_dir, [])] + [(m.path, m.replace_paths) for m in self.mods]:
            for replaced in replace:
                if folder == replaced or folder.startswith(replaced + "/"):
                    files.clear()
            base = root / folder
            if base.is_dir():
                for path in sorted(base.rglob(pattern)):
                    files[path.relative_to(root).as_posix()] = path
        return [files[key] for key in sorted(files)]

    def describe(self) -> dict:
        return {
            "name": self.name,
            "game_dir": str(self.game_dir),
            "mods": [{"name": m.name, "version": m.version, "path": str(m.path)} for m in self.mods],
        }


def _is_bridge(mod: Mod) -> bool:
    return mod.name.strip().lower() in {"llm bridge", "llm_bridge"}


# --- the index ---------------------------------------------------------------


@dataclass
class Focus:
    id: str
    #: All groups must be met; any focus within a group meets it.
    prerequisites: list[list[str]] = field(default_factory=list)


@dataclass
class FocusTree:
    id: str
    tags: set[str] = field(default_factory=set)
    default: bool = False
    focuses: dict[str, Focus] = field(default_factory=dict)
    shared: list[str] = field(default_factory=list)


@dataclass
class StateInfo:
    id: str
    key: str
    name: str
    owner: str | None


@dataclass
class IdentifierIndex:
    playset: Playset
    tags: set[str] = field(default_factory=set)
    trees: list[FocusTree] = field(default_factory=list)
    shared_focuses: dict[str, Focus] = field(default_factory=dict)
    technologies: set[str] = field(default_factory=set)
    states: dict[str, StateInfo] = field(default_factory=dict)

    # --- building -------------------------------------------------------------

    @classmethod
    def build(cls, playset: Playset) -> IdentifierIndex:
        index = cls(playset=playset)
        for path in playset.layered("common/country_tags"):
            for line in _uncommented(path).splitlines():
                match = re.match(r"\s*([A-Z0-9]{3})\s*=", line)
                if match:
                    index.tags.add(match.group(1))
        for path in playset.layered("common/national_focus"):
            index._read_focus_file(_parse(path))
        for path in playset.layered("common/technologies"):
            for block in _as_list(_parse(path).get("technologies")):
                for key, value in block.items():
                    if isinstance(value, dict) and not key.startswith("@") and key != "_items":
                        index.technologies.add(key)
        names = index._state_names()
        for path in playset.layered("history/states"):
            for block in _as_list(_parse(path).get("state")):
                state_id = str(block.get("id", "")).strip()
                if not state_id:
                    continue
                key = str(block.get("name", f"STATE_{state_id}"))
                history = _first(block.get("history")) or {}
                owner = history.get("owner")
                index.states[state_id] = StateInfo(
                    id=state_id, key=key, name=names.get(key, key),
                    owner=str(owner) if isinstance(owner, str) else None,
                )
        return index

    def _read_focus_file(self, parsed: dict) -> None:
        for block in _as_list(parsed.get("shared_focus")):
            focus = _focus(block)
            if focus:
                self.shared_focuses[focus.id] = focus
        for block in _as_list(parsed.get("focus_tree")):
            if not isinstance(block, dict):
                continue
            tree = FocusTree(id=str(block.get("id", "")))
            tree.tags = set(_all_values(block.get("country"), "tag"))
            tree.default = str(block.get("default", "no")) == "yes"
            for entry in _as_list(block.get("focus")):
                focus = _focus(entry)
                if focus:
                    tree.focuses[focus.id] = focus
            tree.shared = [str(s) for s in _as_list(block.get("shared_focus")) if isinstance(s, str)]
            # A mod redefining a tree by id replaces it, as the game does.
            self.trees = [t for t in self.trees if t.id != tree.id or not tree.id]
            self.trees.append(tree)

    def _state_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        files = [
            path for path in self.playset.layered("localisation", "*.yml")
            if "state" in path.name.lower() and "english" in path.as_posix().lower()
        ]
        # A key defined twice keeps its first definition, unless the later one
        # sits in a replace/ folder -- which is how a mod renames a state.
        files.sort(key=lambda path: "replace" in path.parent.parts)
        for path in files:
            replacing = "replace" in path.parent.parts
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for key, value in re.findall(r'^\s*(STATE_\w+):\d*\s*"([^"]*)"', text, flags=re.M):
                if replacing or key not in names:
                    names[key] = value
        return names

    # --- lookups --------------------------------------------------------------

    def trees_for(self, tag: str) -> list[FocusTree]:
        own = [tree for tree in self.trees if tag in tree.tags]
        return own or [tree for tree in self.trees if tree.default]

    def focuses_for(self, tag: str) -> dict[str, Focus]:
        """The focuses ``tag`` can ever take: its trees, plus the shared focuses
        those trees pull in and everything that descends from them."""
        focuses: dict[str, Focus] = {}
        roots: list[str] = []
        for tree in self.trees_for(tag):
            focuses.update(tree.focuses)
            roots += tree.shared
        included = set(roots)
        changed = True
        while changed:
            changed = False
            for focus in self.shared_focuses.values():
                if focus.id in included:
                    continue
                needed = {f for group in focus.prerequisites for f in group}
                if needed & included:
                    included.add(focus.id)
                    changed = True
        focuses.update({k: self.shared_focuses[k] for k in included if k in self.shared_focuses})
        return focuses

    def available_focuses(self, tag: str, completed: Iterable[str]) -> list[str]:
        """Focuses whose every prerequisite group has at least one completed focus."""
        done = set(completed)
        return [
            focus.id
            for focus in self.focuses_for(tag).values()
            if focus.id not in done and all(set(group) & done for group in focus.prerequisites)
        ]

    def resolve_state(self, value: str) -> StateInfo | None:
        text = str(value).strip()
        if text in self.states:
            return self.states[text]
        lowered = text.lower()
        for state in self.states.values():
            if lowered in (state.key.lower(), state.name.lower()):
                return state
        return None

    # --- validation -----------------------------------------------------------

    def check(self, call: ActionCall, country: str) -> ActionResult | None:
        """None when every identifier in the call is real; otherwise a rejection
        naming the nearest real ones."""
        args = call.arguments
        if call.name == "set_national_focus" and self.trees:
            focus = str(args.get("focus_id", ""))
            mine = self.focuses_for(country)
            if focus not in mine:
                anywhere = focus in self.shared_focuses or any(focus in t.focuses for t in self.trees)
                why = (f"'{focus}' is not in {country}'s focus tree" if anywhere
                       else f"There is no focus '{focus}' in this playset ({self.playset.name})")
                return self._reject(call, why, focus, mine)
        if call.name == "start_research" and self.technologies:
            tech = str(args.get("technology", ""))
            if tech not in self.technologies:
                return self._reject(
                    call, f"There is no technology '{tech}' in this playset", tech, self.technologies
                )
        if call.name == "queue_construction" and self.states:
            state = str(args.get("state", ""))
            if state.lower() not in STATE_ALIASES and self.resolve_state(state) is None:
                names = [s.name for s in self.states.values()] + list(self.states)
                return self._reject(call, f"There is no state '{state}' in this playset", state, names)
        argument = TAG_ARGUMENTS.get(call.name)
        if argument and self.tags:
            tag = str(args.get(argument, ""))
            if tag and tag.upper() not in self.tags:
                return self._reject(call, f"There is no country '{tag}' in this playset", tag,
                                    self.tags)
        return None

    def _reject(self, call: ActionCall, why: str, given: str, choices: Iterable[str]) -> ActionResult:
        near = difflib.get_close_matches(given, sorted(set(choices)), n=5, cutoff=0.5)
        hint = f" Nearest real ones: {', '.join(near)}." if near else ""
        return ActionResult(
            ok=False, action=call.name, call_id=call.call_id,
            message=f"{why}.{hint}", error_kind="invalid_args",
        )

    # --- the slice that goes in the brief ------------------------------------

    def brief_lines(self, state: GameState, limit: int = 12) -> list[str]:
        """The focuses this country can start now -- only when it needs one."""
        if state.national_focus or not state.known("national_focus") or not self.trees:
            return []
        focuses = self.focuses_for(state.country)
        if not focuses:
            return []
        if state.known("completed_focuses"):
            options = self.available_focuses(state.country, state.completed_focuses)
            head = "Focuses available now"
        else:
            options = [f.id for f in focuses.values() if not f.prerequisites]
            head = "Focus tree roots (completed focuses not observable)"
        shown = ", ".join(options[:limit])
        more = f" (+{len(options) - limit} more)" if len(options) > limit else ""
        return [f"{head}: {shown}{more}" if options else f"{head}: none"]

    def summary(self) -> dict:
        return {
            "playset": self.playset.name,
            "tags": len(self.tags),
            "focus_trees": len(self.trees),
            "focuses": sum(len(t.focuses) for t in self.trees) + len(self.shared_focuses),
            "technologies": len(self.technologies),
            "states": len(self.states),
            "fingerprint": self.fingerprint(),
        }

    def fingerprint(self) -> str:
        """Short, stable hash of what was indexed: two runs with the same one
        saw the same vocabulary."""
        digest = hashlib.sha1()
        for items in (sorted(self.tags), sorted(self.technologies), sorted(self.states),
                      sorted(f"{t.id}:{','.join(sorted(t.focuses))}" for t in self.trees),
                      sorted(self.shared_focuses)):
            digest.update("\n".join(items).encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()[:12]


# --- helpers -----------------------------------------------------------------


def _uncommented(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    # '#' starts a comment everywhere except inside a quoted string.
    return "\n".join(re.sub(r'#(?=(?:[^"]*"[^"]*")*[^"]*$).*', "", line) for line in text.splitlines())


def _parse(path: Path) -> dict:
    return parse_clausewitz(_uncommented(path))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first(value):
    items = _as_list(value)
    return items[0] if items else None


def _all_values(block, key: str) -> list[str]:
    """Every value of ``key`` anywhere inside ``block``, however deeply nested."""
    found: list[str] = []
    for item in _as_list(block):
        if isinstance(item, dict):
            for k, v in item.items():
                if k == key:
                    found += [str(x) for x in _as_list(v) if isinstance(x, str)]
                else:
                    found += _all_values(v, key)
    return found


def _focus(block) -> Focus | None:
    if not isinstance(block, dict) or "id" not in block:
        return None
    groups = []
    for prerequisite in _as_list(block.get("prerequisite")):
        if isinstance(prerequisite, dict):
            members = [str(f) for f in _as_list(prerequisite.get("focus")) if isinstance(f, str)]
            if members:
                groups.append(members)
    return Focus(id=str(block["id"]), prerequisites=groups)

