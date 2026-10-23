import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

LOG = Path.home() / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "logs" / "game.log"
CMD_FILE = Path.home() / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "turn_cmd.txt"
RUN_LINE = "run turn_cmd.txt"
DIRECTIVES = ["protect", "invade", "contain", "befriend", "antagonize", "ignore"]
POSTURES = ["defensive", "offensive"]
EVENT_NAMES = {1: "war_declared", 2: "capitulation", 10: "posture_defensive",
               11: "posture_offensive", 12: "directives_cleared", 13: "target_selected",
               14: "directive_raised"}

SYSTEM = """You are the supreme command AI for {country} in a Hearts of Iron IV campaign starting 1936.
You do not micromanage. The game's built-in AI executes the economy, research and all military operations.
You steer strategy through a small set of directives:

- posture: "defensive" (hold lines, conserve forces) or "offensive" (press attacks)
- diplomatic directives toward a country tag: {directives}
  - protect: guarantee-like affinity, avoid war with
  - invade / contain: aggression toward the target
  - befriend: improve relations, seek alignment
  - antagonize: hostility, hostile diplomacy
  - ignore: deprioritize the target entirely
- clear_all: drop every directive and posture

The campaign is historical-outcome-neutral: judge the situation on its merits.
Respond ONLY with a JSON object, no other text:
{{"posture": "defensive" or "offensive" or null, "raise": [{{"directive": "<one of the six>", "target": "<TAG>"}}], "clear_all": true or false, "reasoning": "<one short sentence>"}}
Rules: raise at most 3 directives per turn; most turns need none (empty raise list);
keep a directive only while it still serves the plan; if you change posture, set it explicitly."""


def tail_llmb(offset, state, events):
    if not LOG.exists():
        sys.exit("no game.log - is the game running?")
    if LOG.stat().st_size < offset:
        offset = 0
    lines = []
    with LOG.open(encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        for line in handle:
            if "LLMB" in line:
                lines.append(line)
        offset = handle.tell()
    for line in lines:
        if re.search(r"LLMB\|v\d+\|boot\|", line):
            state.clear()
            del events[:]
        m = re.search(r"LLMB\|v\d+\|state\|(.*)$", line)
        if m:
            fields = dict(kv.split("=", 1) for kv in m.group(1).split("|") if "=" in kv)
            fields.pop("date", None)
            state.update(fields)
        dm = re.search(r"\[(\d{4})\.(\d{2})\.(\d{2})\.\d+\]", line)
        if dm:
            state["date"] = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
        em = re.search(r"LLMB\|v\d+\|evt\|.*kind=(\d+)\|detail=(.*)$", line)
        if em:
            events.append({"kind": EVENT_NAMES.get(int(em.group(1)), em.group(1)), "detail": em.group(2).strip()})
    return offset, state, events


def ask_planner(base_url, model, country, state, active, events):
    active_desc = ", ".join(active) if active else "none"
    events_desc = json.dumps(events[-8:], ensure_ascii=False) if events else "[]"
    user = (
        f"Date: {state.get('date', '?')}\n"
        f"Political power: {state.get('pp', '?')} | Stability: {state.get('stab', '?')}% | "
        f"War support: {state.get('ws', '?')}%\n"
        f"Factories: {state.get('civ', '?')} civ / {state.get('mil', '?')} mil / {state.get('doc', '?')} dockyards | "
        f"Manpower: {state.get('mp', '?')} | Divisions: {state.get('divisions', '?')}\n"
        f"Currently active directives: {active_desc}\n"
        f"Recent events: {events_desc}\n"
        f"Decide this turn's directives. /no_think"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM.format(country=country, directives=", ".join(DIRECTIVES))},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 300,
    }
    r = requests.post(f"{base_url}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    jm = re.search(r"\{.*\}", text, re.S)
    if not jm:
        raise ValueError(f"no JSON in planner reply: {text[:200]}")
    return json.loads(jm.group(0))


def translate(decision, days):
    cmds = []
    if decision.get("clear_all"):
        cmds.append("e llm_bridge_clear_directives")
    for item in decision.get("raise", []):
        directive = item.get("directive")
        target = str(item.get("target", "")).upper()
        if directive in DIRECTIVES and re.fullmatch(r"[A-Z]{3}", target):
            cmds.append(f"set_country_flag llmb_target_{target}")
            cmds.append(f"e llm_bridge_raise_{directive}")
    posture = decision.get("posture")
    if posture in POSTURES:
        cmds.append(f"e llm_bridge_set_posture_{posture}")
    cmds.append(f"fast_forward {days}")
    return cmds


CONSOLE_VK = 0xC0
SHIFT_VK = 0x10
ENTER_VK = 0x0D
KEYEVENTF_KEYUP = 0x0002


def _find_hoi4_hwnd():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    result = ctypes.c_void_p()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value.startswith("Hearts of Iron"):
                result.value = hwnd
                return False
        return True

    user32.EnumWindows(callback, 0)
    return result.value


def _send_key(vk, up=False):
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def _type_char(ch):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.VkKeyScanW.restype = ctypes.c_short
    user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
    code = user32.VkKeyScanW(ch)
    vk = code & 0xFF
    shift = bool((code >> 8) & 1)
    if shift:
        _send_key(SHIFT_VK)
    _send_key(vk)
    time.sleep(0.012)
    _send_key(vk, up=True)
    if shift:
        _send_key(SHIFT_VK, up=True)
    time.sleep(0.012)


def send_run(console_vk, run_line):
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = _find_hoi4_hwnd()
    if not hwnd:
        sys.exit("HOI4 window not found")
    focused = user32.GetForegroundWindow() == hwnd
    if not focused:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.6)
        focused = user32.GetForegroundWindow() == hwnd
    print(f"input: hwnd={hwnd} focused={focused}", flush=True)
    _send_key(console_vk)
    time.sleep(0.35)
    for ch in run_line:
        _type_char(ch)
    time.sleep(0.15)
    _send_key(ENTER_VK)
    time.sleep(0.3)
    _send_key(console_vk)


def wait_for_date(offset, state, target_date, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        offset, state, _ = tail_llmb(offset, state, [])
        if state.get("date", "") >= target_date:
            return offset, state
    return offset, state


def next_date(iso, days):
    y, m, d = map(int, iso.split("-"))
    import datetime
    return (datetime.date(y, m, d) + datetime.timedelta(days=days)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="GER")
    ap.add_argument("--turns", type=int, default=5)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--base-url", default="http://127.0.0.1:8081")
    ap.add_argument("--model", default="qwen3-8b")
    ap.add_argument("--human-ai", action="store_true")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    run_line = RUN_LINE
    active = []
    offset, state, events = tail_llmb(0, {}, [])
    if not state.get("date"):
        sys.exit("no LLMB telemetry in game.log yet")
    if args.human_ai:
        CMD_FILE.write_text("human_ai\n", encoding="ascii")
        send_run(CONSOLE_VK, run_line)
        time.sleep(1)
        print("human_ai enabled", flush=True)

    for turn in range(1, args.turns + 1):
        offset, state, events = tail_llmb(offset, state, events)
        if not state.get("date"):
            sys.exit("lost telemetry")
        decision = ask_planner(args.base_url, args.model, args.country, state, active, events)
        print(f"[turn {turn}] {state.get('date')} -> {json.dumps(decision, ensure_ascii=False)}", flush=True)
        for item in decision.get("raise", []):
            active.append(f"{item.get('directive')} {str(item.get('target', '')).upper()}")
        if decision.get("clear_all"):
            active = []
        cmds = translate(decision, args.days)
        CMD_FILE.write_text("\n".join(cmds) + "\n", encoding="ascii")
        send_run(CONSOLE_VK, run_line)
        target = next_date(state["date"], args.days)
        offset, state = wait_for_date(offset, state, target, args.timeout)
        if state.get("date", "0000-00-00") < target:
            print(f"warning: turn {turn} did not reach {target} (fast_forward failed?)")
    print("done")


if __name__ == "__main__":
    main()
