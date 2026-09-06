#!/usr/bin/env python3
"""Assert that a finished run actually played, rather than merely exiting 0.

CI runs the agent for real. The failure this guards against is a run that
completes cleanly while doing nothing -- no actions taken, no clock movement,
every tool call rejected. That is invisible in an exit code and obvious in a
transcript, so this reads the transcript.

Usage: check_run.py runs/direct runs/hybrid ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MIN_ACTIONS = 3
MAX_INVALID_RATE = 0.5


def check(run_dir: Path) -> list[str]:
    problems: list[str] = []
    transcripts = sorted(run_dir.rglob("*.jsonl"))
    if not transcripts:
        return [f"{run_dir}: no transcript was written"]

    for path in transcripts:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not records:
            problems.append(f"{path}: transcript is empty")
            continue

        end = next((r for r in reversed(records) if r["kind"] == "run_end"), None)
        if end is None:
            problems.append(f"{path}: run never reached run_end")
            continue

        ok, failed = end["actions_ok"], end["actions_failed"]
        if ok < MIN_ACTIONS:
            problems.append(f"{path}: only {ok} successful actions (expected >= {MIN_ACTIONS})")
        attempted = ok + failed
        if attempted and failed / attempted > MAX_INVALID_RATE:
            problems.append(
                f"{path}: {failed}/{attempted} actions rejected -- the agent and the "
                "catalog disagree about something"
            )
        if end["start_date"] >= end["end_date"]:
            problems.append(f"{path}: the clock did not advance ({end['start_date']})")
        if end["turns"] < 1:
            problems.append(f"{path}: no turns were played")

        # Hitting a ceiling is a legitimate outcome, not a failure: the loop is
        # supposed to drop to the reflex layer and keep playing. Worth printing,
        # because a run that spent its budget early is not comparable to one that
        # did not.
        blocked = (end.get("spend") or {}).get("blocked")
        if blocked:
            print(f"{path}: note -- {blocked}; the rest of the run was played by the reflex layer")

        print(
            f"{path}: {end['turns']} turns, {end['planner_calls']} model calls, "
            f"{ok} ok / {failed} rejected, {end['start_date']} -> {end['end_date']}"
        )
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_run.py <run-dir> [<run-dir> ...]", file=sys.stderr)
        return 2
    problems = [p for directory in argv for p in check(Path(directory))]
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
