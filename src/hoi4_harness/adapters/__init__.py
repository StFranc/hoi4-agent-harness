"""Adapter registry."""

from __future__ import annotations

from ..config import HarnessConfig
from .base import AdapterInfo, GameAdapter
from .composite import CompositeAdapter
from .mock import MockAdapter

__all__ = [
    "ADAPTERS",
    "AdapterInfo",
    "CompositeAdapter",
    "GameAdapter",
    "MockAdapter",
    "build_adapter",
]

ADAPTERS = (
    "mock",
    "savegame",
    "screen",
    "logtail",
    "logtail+input",
    "savegame+input",
    "screen+input",
)


def build_adapter(config: HarnessConfig) -> GameAdapter:
    """Construct the adapter named by ``config.adapter``.

    Heavier adapters are imported lazily, so the mock path -- what the tests and
    the offline demo use -- never pulls an optional dependency.
    """
    name = config.adapter.strip().lower()
    if name == "mock":
        return MockAdapter(
            seed=config.seed, country=config.country, start=config.start_date
        )
    if name == "savegame":
        from .savegame import SaveGameAdapter

        return SaveGameAdapter(config.save_dir)
    if name == "screen":
        from .screen import ScreenAdapter

        return ScreenAdapter(config.window_title)
    if name == "logtail":
        from .logtail import LogTailAdapter

        return LogTailAdapter(config.log_path, country=config.country)
    if "+" in name:
        read_name, _ = name.split("+", 1)
        from .input_driver import InputConfig, InputDriverAdapter

        reader = build_adapter(
            HarnessConfig(
                adapter=read_name,
                save_dir=config.save_dir,
                log_path=config.log_path,
                country=config.country,
                window_title=config.window_title,
            )
        )
        writer = InputDriverAdapter(
            InputConfig(
                window_title=config.window_title,
                enforce_focus=config.enforce_window_focus,
            ),
            dry_run=config.dry_run,
        )
        return CompositeAdapter(reader, writer, clock_owner=config.clock_owner)
    raise ValueError(f"Unknown adapter {name!r}. Known: {', '.join(ADAPTERS)}")
