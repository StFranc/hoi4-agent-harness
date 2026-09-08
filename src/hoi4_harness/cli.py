"""Command line entry point.

    hoi4-harness doctor                     what is installed and reachable
    hoi4-harness actions                    the agent's vocabulary
    hoi4-harness observe                    one situation brief, then exit
    hoi4-harness play --turns 20            run the loop
    hoi4-harness replay run.jsonl --turn 3  rebuild a turn's prompt, compare models
    hoi4-harness eval economy_ramp          run a scenario and score it

Everything defaults to the mock adapter and the scripted model, so a fresh clone
does something useful with no API key and no game installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .actions import catalog
from .adapters import ADAPTERS, build_adapter
from .agent.llm import PROVIDERS, build_llm
from .agent.loop import AgentLoop
from .agent.memory import Memory
from .config import HarnessConfig
from .env import HOI4Env
from .guidance import available as guidance_packs
from .replay import ReplayError, list_turns, replay_turn


def _config_from_args(args: argparse.Namespace) -> HarnessConfig:
    # Precedence, lowest first: defaults -> environment -> profile file -> flags.
    profile = getattr(args, "profile", None)
    config = HarnessConfig.from_file(profile) if profile else HarnessConfig.from_env()
    if getattr(args, "adapter", None):
        config.adapter = args.adapter
    if getattr(args, "provider", None):
        config.planner.provider = args.provider
        config.planner.model = args.model or config.planner.model or ""
        config.planner.__post_init__()
    if getattr(args, "model", None):
        config.planner.model = args.model
    if getattr(args, "base_url", None):
        config.planner.base_url = args.base_url
    if getattr(args, "effort", None):
        config.planner.effort = args.effort
    if getattr(args, "turns", None):
        config.turns = args.turns
    if getattr(args, "days", None):
        config.days_per_turn = args.days
    if getattr(args, "max_usd", None) is not None:
        config.budget.max_usd = args.max_usd
    if getattr(args, "live", False):
        config.dry_run = False
    if getattr(args, "no_window_guard", False):
        config.enforce_window_focus = False
    if getattr(args, "run_dir", None):
        config.run_dir = Path(args.run_dir)
    if getattr(args, "guidance", None):
        config.guidance = args.guidance
    if getattr(args, "system_prompt", None):
        config.system_prompt_path = Path(args.system_prompt)
    if getattr(args, "objective", None):
        config.objective = args.objective
    if getattr(args, "actions", None):
        config.enabled_actions = [name.strip() for name in args.actions.split(",") if name.strip()]
    if getattr(args, "without", None):
        config.disabled_actions = [name.strip() for name in args.without.split(",") if name.strip()]
    if getattr(args, "allow_all", False):
        config.require_confirmation = False
    if getattr(args, "no_reflex", False):
        config.reflex_enabled = False
    if getattr(args, "player_clock", False):
        config.clock_owner = "player"
    if getattr(args, "country", None):
        config.country = args.country
    if getattr(args, "start_date", None):
        config.start_date = args.start_date
    if getattr(args, "seed", None) is not None:
        config.seed = args.seed
    return config


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    print(f"hoi4-agent-harness {__version__}")
    print(f"python           {sys.version.split()[0]}")
    print(f"adapter          {config.adapter}")
    print(f"planner          {config.planner.provider}:{config.planner.model}")
    print(f"dry run          {config.dry_run}")
    print(f"clock owner      {config.clock_owner}")
    print(f"guidance         {config.system_prompt_path or config.guidance}")
    print(f"confirm gate     {config.require_confirmation}")

    for module, extra in (
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("google.genai", "google"),
        ("pyautogui", "input"),
        ("mss", "input"),
    ):
        try:
            __import__(module)
            print(f"  [x] {module}")
        except ImportError:
            print(f"  [ ] {module}  (pip install 'hoi4-agent-harness[{extra}]')")

    try:
        from .adapters.window import check_focus

        focus = check_focus(config.window_title)
        if focus.supported:
            print(f"game focused     {focus.focused}" + (f" ({focus.title})" if focus.title else ""))
        else:
            print(f"game focused     unknown -- {focus.reason}")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        print(f"game focused     check failed: {exc}")

    try:
        adapter = build_adapter(config)
        info = adapter.info()
        print(f"adapter ok       {info.name}: read={info.readable} write={info.writable}")
        print(f"                 {info.notes}")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        print(f"adapter FAILED   {type(exc).__name__}: {exc}")
        return 1
    return 0


def cmd_actions(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([spec.__dict__ for spec in catalog.ACTIONS], indent=2, default=str))
        return 0
    for spec in catalog.ACTIONS:
        flag = " [confirm]" if spec.requires_confirmation else ""
        required = ", ".join(spec.parameters.get("required", []))
        print(f"{spec.name}{flag}  ({spec.category})")
        print(f"    {spec.description}")
        print(f"    required: {required or '-'}")
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    env = HOI4Env(build_adapter(config), config)
    observation = env.reset()
    print(observation.brief)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    env = HOI4Env(build_adapter(config), config)
    env.reset()
    run_dir = config.run_dir
    resume = bool(getattr(args, "resume", False))
    loop = AgentLoop(
        env=env,
        planner=build_llm(config.planner),
        config=config,
        memory=Memory.load(run_dir / "memory.json"),
        transcript_path=run_dir / "transcript.jsonl",
        resume=resume,
    )
    if resume and loop.report.resumed_from:
        print(f"resuming {run_dir}: {loop.report.turns} turns already played", file=sys.stderr)
    report = loop.run(config.turns)
    loop.memory.save(run_dir / "memory.json")
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        if args.list_turns:
            print(list_turns(Path(args.transcript)))
            return 0
        if args.turn is None:
            print("replay: pass --turn N to choose a turn (see --list)", file=sys.stderr)
            return 2
        config = _config_from_args(args)
        print(replay_turn(Path(args.transcript), args.turn, config).render())
        return 0
    except (ReplayError, ValueError) as exc:
        # ValueError: a misconfigured provider reaches build_llm here, and its
        # message already says what is wrong and what the options are.
        print(f"replay: {exc}", file=sys.stderr)
        return 1


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval import SCENARIOS, run_scenario

    config = _config_from_args(args)
    keys = [args.scenario] if args.scenario else list(SCENARIOS)
    failed = 0
    for key in keys:
        card = run_scenario(key, config, transcript_dir=config.run_dir)
        print(card.render())
        print()
        failed += card.score < 1.0
    return 0 if not args.strict else min(failed, 1)


def cmd_prompt(args: argparse.Namespace) -> int:
    """Print the exact system prompt a run would use. Nothing is hidden."""
    from .agent.prompts import build_system

    config = _config_from_args(args)
    print(
        build_system(
            guidance=config.guidance,
            extra=config.system_prompt_extra,
            system_prompt_path=config.system_prompt_path,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hoi4-harness", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--adapter", choices=ADAPTERS)
        p.add_argument("--provider", choices=PROVIDERS)
        p.add_argument("--model")
        p.add_argument("--base-url", dest="base_url")
        p.add_argument("--effort", choices=["low", "medium", "high"])
        p.add_argument("--run-dir", dest="run_dir")
        p.add_argument("--live", action="store_true",
                       help="allow irreversible actions (default: dry run)")
        p.add_argument("--max-usd", type=float)
        p.add_argument("--no-window-guard", dest="no_window_guard", action="store_true",
                       help="send input even when the game is not the focused window")
        p.add_argument("--profile", help="JSON/TOML file holding a whole configuration")
        p.add_argument("--guidance", metavar="NAME|PATH",
                       help="how much to coach the model: " + ", ".join(guidance_packs())
                            + ", or a path to your own file")
        p.add_argument("--system-prompt", dest="system_prompt", metavar="PATH",
                       help="replace the entire system prompt, mechanics included")
        p.add_argument("--objective", help="the standing objective given every turn")
        p.add_argument("--actions", metavar="A,B,C", help="only allow these actions")
        p.add_argument("--without", metavar="A,B,C", help="forbid these actions")
        p.add_argument("--allow-all", dest="allow_all", action="store_true",
                       help="skip the confirmation gate on irreversible actions")
        p.add_argument("--no-reflex", dest="no_reflex", action="store_true",
                       help="disable the deterministic reflex layer; the model decides everything")
        p.add_argument("--player-clock", dest="player_clock", action="store_true",
                       help="a person is playing: never pause, resume or set game speed")
        p.add_argument("--country", help="country tag (mock adapter)")
        p.add_argument("--start-date", dest="start_date", help="start date (mock adapter)")
        p.add_argument("--seed", type=int, help="mock adapter seed")

    doctor = sub.add_parser("doctor", help="check the environment")
    common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    actions = sub.add_parser("actions", help="list the action catalog")
    actions.add_argument("--json", action="store_true")
    actions.set_defaults(func=cmd_actions)

    prompt = sub.add_parser("prompt", help="print the system prompt a run would use")
    common(prompt)
    prompt.set_defaults(func=cmd_prompt)

    observe = sub.add_parser("observe", help="print one situation brief")
    common(observe)
    observe.set_defaults(func=cmd_observe)

    play = sub.add_parser("play", help="run the agent loop")
    common(play)
    play.add_argument("--turns", type=int)
    play.add_argument("--days", type=int, help="in-game days per turn")
    play.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted run in --run-dir, rebuilding state from its transcript",
    )
    play.set_defaults(func=cmd_play)

    replay = sub.add_parser("replay", help="replay one turn from a transcript and compare models")
    replay.add_argument("transcript", metavar="PATH", help="a transcript.jsonl written by play or eval")
    replay.add_argument("--turn", type=int, help="which planner turn to replay (see --list)")
    replay.add_argument("--list", dest="list_turns", action="store_true",
                        help="show the turns in the transcript, then exit")
    common(replay)
    replay.set_defaults(func=cmd_replay)

    evaluate = sub.add_parser("eval", help="run a scenario and score it")
    common(evaluate)
    evaluate.add_argument("scenario", nargs="?")
    evaluate.add_argument("--strict", action="store_true", help="exit 1 unless every objective passes")
    evaluate.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
