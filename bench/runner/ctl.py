"""Start, follow, inspect or stop the measurement (behind ./measure).

The run is detached from the terminal (its own session), so it continues if
the SSH session that started it closes. Its state is data/ui/status.json.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

R = os.environ.get("BENCH_ROOT", "/opt/bench")
STATUS = os.path.join(R, "data", "ui", "status.json")
PIDFILE = os.path.join(R, "data", "capacity", "measure.pid")
LOG = os.path.join(R, "data", "capacity", "measure-console.log")
DONE = {"done", "refused", "no-capacity", "stopped", "failed"}


def _pid() -> int | None:
    try:
        pid = int(open(PIDFILE).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _status() -> dict:
    try:
        return json.load(open(STATUS))
    except (OSError, ValueError):
        return {}


def _line(s: dict) -> str:
    el = time.time() - s.get("started", time.time())
    msg = f"[{int(el // 3600)}:{int(el % 3600 // 60):02d}] {s.get('message', '')}"
    cur = s.get("current")
    if cur:
        left = cur["hold_s"] - (time.time() - cur["started"])
        msg += f" ({max(0, left) / 60:.0f} min left in this step)"
    return msg


def start(args: list[str]) -> None:
    if _pid():
        print("A measurement is already running; ./measure follow shows it, ./measure stop ends it.")
        return
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
    p = subprocess.Popen([sys.executable, "-m", "runner.measure", *args], cwd=R, start_new_session=True,
                         stdout=open(LOG, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    open(PIDFILE, "w").write(str(p.pid))
    print("Measurement started. It takes about 1 h 50 min and keeps running if this session closes.")
    port = os.environ.get("UI_PORT", "8766")
    print(f"Page: ssh -L {port}:127.0.0.1:{port} <this server>, then http://127.0.0.1:{port}")


def follow() -> None:
    last, steps = None, 0
    while True:
        s = _status()
        line = _line(s) if s else "waiting for the run to start"
        if s.get("message") != last:
            print(line, flush=True)
            last = s.get("message")
        for st in s.get("steps", [])[steps:]:
            print(f"    step {st['step']}: {st['rate']} workflows/s -> {'kept up' if st['passed'] else 'fell behind'}", flush=True)
        steps = len(s.get("steps", []))
        if s.get("phase") in DONE and not _pid():
            out = open(LOG).read().strip().splitlines() if os.path.exists(LOG) else []
            print("\n".join(out[-10:]))
            return
        if not _pid() and s.get("phase") not in DONE:
            print("The run is not running. Last output:")
            print(open(LOG).read()[-2000:] if os.path.exists(LOG) else "(none)")
            return
        time.sleep(15)


def status() -> None:
    s = _status()
    print(_line(s) if s else "No run yet. Start one with ./measure")
    for st in s.get("steps", []):
        print(f"    step {st['step']}: {st['rate']} workflows/s -> {'kept up' if st['passed'] else 'fell behind'}")
    print("running" if _pid() else "not running")


def stop() -> None:
    pid = _pid()
    if not pid:
        print("No measurement is running.")
        return
    os.killpg(pid, signal.SIGTERM)
    for _ in range(30):
        if not _pid():
            break
        time.sleep(1)
    if _pid():
        os.killpg(pid, signal.SIGKILL)
    # Fleet processes the run detached (rerank servers use setsid): end them by
    # explicit pid, never by a pattern a shell's own command line could match.
    me = {os.getpid(), os.getppid()}
    marks = (b"backend.main:app", b"scripts.mock_router", b"ort_rerank_server", b"mpstat", b"pidstat")
    uid = os.getuid()
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in me:
            continue
        try:
            if os.stat(f"/proc/{d}").st_uid != uid:
                continue
            cmd = open(f"/proc/{d}/cmdline", "rb").read()
        except OSError:
            continue
        if any(m in cmd for m in marks):
            try:
                os.kill(int(d), signal.SIGKILL)
            except OSError:
                pass
    s = _status()
    if s:
        s.update(phase="stopped", message="Stopped by ./measure stop")
        json.dump(s, open(STATUS, "w"))
    print("Stopped.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": lambda: start(sys.argv[2:]), "follow": follow, "status": status, "stop": stop}[cmd]()
