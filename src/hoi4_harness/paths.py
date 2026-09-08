r"""Finding the game's user directory.

Every Paradox title keeps saves, logs and mods under a per-user directory. On
Windows that is *supposed* to be ``Documents\Paradox Interactive\...`` -- but
Documents is very often redirected into OneDrive, and then it is not there at
all. Checked against a real install: the only copy on that machine lives under
``OneDrive\Documents``, and every default path the harness had would have missed
it.

Hence one place that knows where to look, rather than two lists that drift.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

GAME_DIR = Path("Paradox Interactive") / "Hearts of Iron IV"


def documents_roots() -> Iterator[Path]:
    """Every plausible Documents folder, redirected or not.

    Order matters only for speed: the first hit wins, and a machine with both a
    redirected and a local Documents usually has the real install in the
    redirected one.
    """
    seen: set[Path] = set()
    home = Path.home()
    profile = Path(os.environ.get("USERPROFILE", "")) if os.environ.get("USERPROFILE") else None
    onedrive = Path(os.environ["OneDrive"]) if os.environ.get("OneDrive") else None

    for base in (onedrive, home, profile):
        if base is None:
            continue
        for candidate in (base / "Documents", base / "OneDrive" / "Documents", base):
            if candidate not in seen:
                seen.add(candidate)
                yield candidate


def game_dirs() -> Iterator[Path]:
    """Candidate ``Hearts of Iron IV`` user directories, existing or not."""
    for root in documents_roots():
        yield root / GAME_DIR


def find_game_dir(explicit: Path | None = None) -> Path | None:
    """An explicit path is used or it fails; it never falls back to discovery.

    Silently searching elsewhere after the operator named a directory would mean
    reading a different campaign than the one they pointed at.
    """
    if explicit is not None:
        return explicit if explicit.is_dir() else None
    for candidate in game_dirs():
        if candidate.is_dir():
            return candidate
    return None


def find_in_game_dir(*parts: str, explicit: Path | None = None) -> Path | None:
    """First existing ``<game dir>/<parts...>``, or None.

    ``explicit`` is taken as the final path itself, not as a game directory, so
    HOI4_LOG_PATH and HOI4_SAVE_DIR keep pointing straight at a file or folder.
    """
    if explicit is not None:
        # Same rule as find_game_dir: what was named, or nothing.
        return explicit if explicit.exists() else None
    for game_dir in game_dirs():
        candidate = game_dir.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None
