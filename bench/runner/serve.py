"""The page, on 127.0.0.1:8766 only (reach it through an SSH tunnel).

    /                  the replay of this server's results; the run status until a run has results
    /status            readiness, the live run and its load steps
    /replay.html       the replay itself
    /fonts.css         Roboto (the replay's embedded faces)
    /status.json       the run's live state (runner.status)
    /preflight.json    the readiness check, run when the container starts
    /results.json      this server's results (after a run)
    /reference.json    the published PowerEdge R770 results, for comparison
    /calibration.json  calibration.yaml, read on every request (edit, reload)
    /live.json         host CPU busy and memory in use, sampled every 2 s
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from runner import preflight

R = os.environ.get("BENCH_ROOT", "/opt/bench")
KIT = os.environ.get("KIT_ROOT", "/opt/kit")
UI = os.path.join(R, "data", "ui")
APP = os.path.join(R, "ui")
PORT = int(os.environ.get("UI_PORT", "8766"))
_live = {"cpu_pct": None, "mem_used_gb": None, "mem_total_gb": None, "ts": 0}


def _sample_loop() -> None:
    def snap():
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(f), f[3] + f[4]
    prev = snap()
    while True:
        time.sleep(2)
        cur = snap()
        dt_, di = cur[0] - prev[0], cur[1] - prev[1]
        prev = cur
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        _live.update(cpu_pct=round(100 * (1 - di / max(1, dt_)), 1),
                     mem_used_gb=round((mem["MemTotal"] - mem["MemAvailable"]) / 1024 ** 2, 1),
                     mem_total_gb=round(mem["MemTotal"] / 1024 ** 2), ts=time.time())


def _preflight() -> None:
    try:
        pf = preflight.check(quick=True)
    except Exception as exc:  # noqa: BLE001
        pf = {"ok": False, "summary": f"preflight failed: {exc}", "checks": [], "system": preflight.system_info()}
    os.makedirs(UI, exist_ok=True)
    json.dump(pf, open(os.path.join(UI, "preflight.json"), "w"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, ctype: str) -> None:
        try:
            self._send(200, open(path, "rb").read(), ctype)
        except OSError:
            self._send(404, b'{"missing": true}', "application/json")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            # The replay of this server's results is the page; before the first
            # results exist, the run status (readiness and progress) stands in.
            replay = os.path.join(UI, "replay.html")
            return self._file(replay if os.path.exists(replay) else os.path.join(APP, "index.html"), "text/html; charset=utf-8")
        if path in ("/status", "/status.html"):
            return self._file(os.path.join(APP, "index.html"), "text/html; charset=utf-8")
        if path == "/assets.json":     # logos for the status page header, shared with the replay
            return self._file(os.path.join(APP, "replay", "assets.json"), "application/json")
        if path == "/fonts.css":      # Roboto, embedded once for both pages
            return self._file(os.path.join(APP, "replay", "fonts.css"), "text/css; charset=utf-8")
        if path == "/replay.html":
            return self._file(os.path.join(UI, "replay.html"), "text/html; charset=utf-8")
        if path in ("/status.json", "/preflight.json", "/results.json"):
            return self._file(os.path.join(UI, path[1:]), "application/json")
        if path == "/reference.json":
            return self._file(os.path.join(KIT, "calibration", "reference-r770.json"), "application/json")
        if path == "/calibration.json":
            try:
                body = json.dumps(yaml.safe_load(open(os.path.join(KIT, "calibration.yaml")))).encode()
                return self._send(200, body, "application/json")
            except Exception as exc:  # noqa: BLE001
                return self._send(500, json.dumps({"error": f"calibration.yaml: {exc}"}).encode(), "application/json")
        if path == "/live.json":
            return self._send(200, json.dumps(_live).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


def main() -> None:
    threading.Thread(target=_sample_loop, daemon=True).start()
    threading.Thread(target=_preflight, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"page on http://127.0.0.1:{PORT} (ssh -L {PORT}:127.0.0.1:{PORT} <this server>)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
