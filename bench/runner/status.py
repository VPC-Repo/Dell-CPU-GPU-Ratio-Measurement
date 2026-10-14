"""The run's live state for the page: one JSON file, replaced atomically."""
from __future__ import annotations

import json
import os
import time


class Status:
    def __init__(self, path: str, **fields):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.s = {"started": time.time(), "phase": "starting", "message": "", "steps": [], **fields}
        self._write()

    def _write(self) -> None:
        self.s["updated"] = time.time()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.s, fh)
        os.replace(tmp, self.path)

    def update(self, **fields) -> None:
        self.s.update(fields)
        self._write()

    def phase(self, phase: str, message: str, **fields) -> None:
        self.update(phase=phase, message=message, **fields)

    def step(self, n: int, rate_per_instance: float, instances: int, hold_s: int) -> None:
        self.update(phase="load", message=f"Load step {n}: {rate_per_instance * instances:.2f} workflows/s",
                    current={"step": n, "rate": round(rate_per_instance * instances, 3), "started": time.time(),
                             "hold_s": hold_s})

    def step_done(self, step: dict) -> None:
        self.s["steps"].append(step)
        self.s.pop("current", None)
        self._write()
