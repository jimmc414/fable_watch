#!/usr/bin/env python3
"""fable_watch -- watch for Claude Fable 5 coming back online, and shout when it does.

Background
----------
On 2026-06-12 a US government export-control directive forced Anthropic to pull
Claude Fable 5 (and Mythos 5) offline worldwide. Anthropic leadership went to the
White House on 2026-06-15 to resolve it; the stated hope is that the model goes
back into general release. This tool probes the model on a schedule and fires
every configured notification channel the instant it answers normally again.

Detection
---------
A probe runs `claude --model <model> -p "<prompt>"` using your Max OAuth
credentials. ANTHROPIC_API_KEY is stripped from the child environment so billing
stays on the subscription (never API). While suspended, the CLI replies with a
sentinel ("currently unavailable") at exit code 0 -- so we match on response
*content*, not the exit code:
    online  -> normal reply, no sentinel
    offline -> sentinel present
    error   -> timeout / CLI missing / empty output (treated as "keep waiting")

Only an explicit `online` result triggers notifications, so transient errors and
token refreshes never produce a false alarm.

Usage
-----
    python fable_watch.py start         # start the background daemon
    python fable_watch.py status        # show daemon + watch status
    python fable_watch.py stop          # stop the daemon
    python fable_watch.py run           # watch in the foreground (Ctrl-C to stop)
    python fable_watch.py once          # single probe; exit 0=online 2=offline 3=error
    python fable_watch.py test-notify   # send a test alert through active channels
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from config import PROJECT_DIR, load_config
from notify import Notification, channel_status, dispatch

STATE_PATH = PROJECT_DIR / "state.json"
PID_PATH = PROJECT_DIR / "fable_watch.pid"
LOG_PATH = PROJECT_DIR / "fable_watch.log"

_stop = threading.Event()


# --- probe -------------------------------------------------------------------

@dataclass
class ProbeResult:
    status: str   # "online" | "offline" | "error"
    detail: str
    raw: str


def probe(config: dict, logger: logging.Logger) -> ProbeResult:
    w = config["watch"]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # house rule: force Max OAuth, never API billing
    cmd = ["claude", "--model", w["model"], "-p", w["probe_prompt"]]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=w["probe_timeout_seconds"], env=env,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult("error", f"timeout after {w['probe_timeout_seconds']}s", "")
    except FileNotFoundError:
        return ProbeResult("error", "`claude` CLI not found on PATH", "")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    haystack = f"{out}\n{err}".lower()
    for sentinel in w["offline_sentinels"]:
        if sentinel.lower() in haystack:
            return ProbeResult("offline", f"sentinel: {sentinel!r}", out or err)
    if proc.returncode == 0 and out:
        return ProbeResult("online", "normal reply", out)
    return ProbeResult("error", f"rc={proc.returncode}, empty/unrecognized output", out or err)


# --- state -------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (ValueError, OSError):
            pass
    return {
        "checks": 0, "last_status": None, "last_check": None,
        "consecutive_offline": 0, "online_since": None, "notified": False,
    }


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


# --- logging -----------------------------------------------------------------

def setup_logging(to_stream: bool) -> logging.Logger:
    logger = logging.getLogger("fable_watch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if to_stream:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)
    return logger


# --- watch loop --------------------------------------------------------------

def _handle_signal(signum, frame):
    _stop.set()


def watch_loop(config: dict, logger: logging.Logger) -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    w = config["watch"]
    state = load_state()
    logger.info("fable_watch started -- model=%s interval=%ss", w["model"], w["interval_seconds"])
    for name, (enabled, active, reason) in channel_status(config).items():
        if enabled:
            logger.info(
                "channel %-13s %s", name,
                "active" if active else f"INACTIVE ({reason})",
            )

    while not _stop.is_set():
        result = probe(config, logger)
        now = datetime.now().isoformat(timespec="seconds")
        prev = state["last_status"]

        state["checks"] += 1
        state["last_check"] = now
        state["last_status"] = result.status
        state["consecutive_offline"] = (
            state["consecutive_offline"] + 1 if result.status == "offline" else 0
        )
        logger.info("check #%d: %s (%s)", state["checks"], result.status, result.detail)

        if result.status == "online" and prev != "online":
            state["online_since"] = now
            state["notified"] = True
            logger.info("Fable is ONLINE -- firing notifications")
            note = Notification(
                title="Claude Fable 5 is BACK ONLINE",
                message=(
                    f"{w['model']} answered a probe normally at {now}.\n"
                    f"Run  /model fable  in Claude Code to use it again."
                ),
                url="https://www.anthropic.com/news/fable-mythos-access",
            )
            dispatch(config, note, logger)
            save_state(state)
            if w["stop_when_online"]:
                logger.info("stop_when_online=true -- exiting after success.")
                break

        save_state(state)
        _stop.wait(w["interval_seconds"])  # interruptible sleep

    logger.info("fable_watch stopped.")


# --- daemon plumbing ---------------------------------------------------------

def daemonize() -> None:
    """Detach into the background via the classic double-fork."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir(str(PROJECT_DIR))
    with open(os.devnull, "rb") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    logf = open(LOG_PATH, "a", buffering=1)
    os.dup2(logf.fileno(), sys.stdout.fileno())
    os.dup2(logf.fileno(), sys.stderr.fileno())


def read_pid() -> int | None:
    if PID_PATH.exists():
        try:
            return int(PID_PATH.read_text().strip())
        except ValueError:
            return None
    return None


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def print_channel_summary(config: dict) -> None:
    print("channels:")
    for name, (enabled, active, reason) in channel_status(config).items():
        if not enabled:
            label = "off"
        elif active:
            label = "ON"
        else:
            label = f"enabled but INACTIVE -- {reason}"
        print(f"  {name:14} {label}")


# --- commands ----------------------------------------------------------------

def cmd_start(config: dict) -> int:
    if not hasattr(os, "fork"):  # native Windows (not WSL)
        print("The background daemon needs POSIX fork (Linux / macOS / WSL).")
        print("On native Windows, run it in the foreground instead:")
        print("    python fable_watch.py run")
        print("and keep it alive with Task Scheduler or a terminal that stays open.")
        return 1
    pid = read_pid()
    if pid and is_running(pid):
        print(f"fable_watch is already running (pid {pid}).")
        return 1
    w = config["watch"]
    print(f"Starting fable_watch -- watching {w['model']} every {w['interval_seconds']}s.")
    print(f"Log:   {LOG_PATH}")
    print(f"State: {STATE_PATH}")
    print_channel_summary(config)
    print("Daemon detached. Use 'status' to check, 'stop' to end.")

    daemonize()
    PID_PATH.write_text(str(os.getpid()))
    logger = setup_logging(to_stream=False)
    try:
        watch_loop(config, logger)
    finally:
        PID_PATH.unlink(missing_ok=True)
    return 0


def cmd_stop(config: dict) -> int:
    pid = read_pid()
    if not pid or not is_running(pid):
        print("fable_watch is not running.")
        PID_PATH.unlink(missing_ok=True)
        return 1
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not is_running(pid):
            break
        time.sleep(0.1)
    print(f"Stopped fable_watch (pid {pid}).")
    return 0


def cmd_status(config: dict) -> int:
    pid = read_pid()
    running = bool(pid and is_running(pid))
    state = load_state()
    w = config["watch"]
    print(f"daemon:       {'running (pid %d)' % pid if running else 'stopped'}")
    print(f"model:        {w['model']}")
    print(f"interval:     {w['interval_seconds']}s")
    print(f"config:       {config['_config_path']}"
          f"{'' if config['_config_exists'] else '  (not found -- using defaults)'}")
    print(f"checks run:   {state['checks']}")
    print(f"last status:  {state['last_status']} @ {state['last_check'] or 'never'}")
    if state.get("online_since"):
        print(f"ONLINE since: {state['online_since']}")
    print_channel_summary(config)
    return 0


def cmd_run(config: dict) -> int:
    logger = setup_logging(to_stream=True)
    try:
        watch_loop(config, logger)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_once(config: dict) -> int:
    logger = setup_logging(to_stream=True)
    result = probe(config, logger)
    logger.info("probe: %s (%s)", result.status, result.detail)
    print(f"\n{config['watch']['model']}: {result.status.upper()} -- {result.detail}")
    if result.raw:
        print(f"raw: {result.raw[:200]}")
    return {"online": 0, "offline": 2, "error": 3}[result.status]


def cmd_test_notify(config: dict) -> int:
    logger = setup_logging(to_stream=True)
    note = Notification(
        title="fable_watch test notification",
        message=("This is a TEST -- Fable is not actually online.\n"
                 "If you received this, the channel works."),
        url="https://www.anthropic.com/news/fable-mythos-access",
    )
    results = dispatch(config, note, logger)
    print("\nResults:")
    if not results:
        print("  (no channels enabled)")
    for name, ok in results.items():
        print(f"  {name:14} {'ok' if ok else 'FAILED / skipped'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fable_watch",
        description="Watch for Claude Fable 5 returning online and notify when it does.",
    )
    parser.add_argument("--config", help="path to a config.toml (default: ./config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("start", "start the background daemon"),
        ("stop", "stop the background daemon"),
        ("status", "show daemon + watch status"),
        ("run", "watch in the foreground (Ctrl-C to stop)"),
        ("once", "run a single probe and exit"),
        ("test-notify", "send a test alert through active channels"),
    ]:
        sub.add_parser(name, help=help_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    dispatch_table = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "run": cmd_run,
        "once": cmd_once,
        "test-notify": cmd_test_notify,
    }
    return dispatch_table[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
