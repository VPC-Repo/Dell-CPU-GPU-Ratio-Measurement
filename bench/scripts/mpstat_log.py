"""Shared readers for a run's per-core samples and the host's core topology.

mpstat prints wall-clock times in the sampled host's local time, with the
date only in its header. `samples()` returns UTC epochs:

- the zone comes from the `MPSTAT_TZ` environment variable, else a
  `timezone.txt` beside the log (written by scripts/r770_run.sh), else the
  zone of the machine running the analysis; it is DST-aware (zoneinfo)
- 12-hour (AM/PM), 24-hour and ISO (`S_TIME_FORMAT=ISO`) stamps all read
- a log that runs past midnight advances its date when the clock wraps

`topology()` reads `topology.txt` beside the log when present (also written
by the runner), so a run can be analysed away from the host; otherwise it
reads this machine's /sys.
"""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import os
import re
from zoneinfo import ZoneInfo


def _open(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def zone(log: str):
    name = os.getenv("MPSTAT_TZ")
    if not name:
        p = os.path.join(os.path.dirname(os.path.abspath(log)), "timezone.txt")
        if os.path.exists(p):
            name = open(p).read().strip()
    return ZoneInfo(name) if name else None   # None: this machine's local zone


def samples(log: str) -> dict[int, dict[int, float]]:
    """{utc epoch: {cpu: busy %}} for every per-CPU row of the log."""
    tz = zone(log)
    out: dict[int, dict[int, float]] = {}
    day, last, cpu_col = None, None, None
    for line in _open(log):
        f = line.split()
        if not f:
            continue
        if f[0] == "Linux":
            m = re.search(r"(\d\d)/(\d\d)/(\d{2,4})|(\d{4})-(\d\d)-(\d\d)", line)
            if m and m.group(1):
                y = int(m.group(3)); y += 2000 if y < 100 else 0
                day = dt.date(y, int(m.group(1)), int(m.group(2)))
            elif m:
                day = dt.date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            last = None
            continue
        if f[0] == "Average:" or day is None:
            continue
        if "CPU" in f and "%idle" in f:
            cpu_col = f.index("CPU")
            continue
        if cpu_col is None or len(f) <= cpu_col:
            continue
        hms = f[0]
        if f[1] in ("AM", "PM"):
            h, m_, s = (int(x) for x in hms.split(":"))
            h = h % 12 + (12 if f[1] == "PM" else 0)
        else:
            h, m_, s = (int(x) for x in hms.split(":"))
        clock = h * 3600 + m_ * 60 + s
        if last is not None and clock < last - 12 * 3600:
            day += dt.timedelta(days=1)          # the log ran past midnight
        last = clock
        local = dt.datetime(day.year, day.month, day.day, h, m_, s, tzinfo=tz)
        ts = int(local.timestamp())
        tag = f[cpu_col]
        if tag == "all":
            out.setdefault(ts, {})
        elif tag.isdigit():
            try:
                out.setdefault(ts, {})[int(tag)] = 100.0 - float(f[-1])
            except ValueError:
                pass
    return out


def topology(log: str | None = None, sockets: set[int] | None = None) -> dict[tuple[int, int], list[int]]:
    """{(package, core_id): [logical cpus]}, optionally limited to some sockets."""
    cores: dict[tuple[int, int], list[int]] = {}
    p = os.path.join(os.path.dirname(os.path.abspath(log)), "topology.txt") if log else None
    if p and os.path.exists(p):
        for line in open(p):
            m = re.match(r"(\d+):\S+ pkg=(\d+) core=(\d+)", line)
            if m:
                cpu, pkg, cid = (int(x) for x in m.groups())
                if not sockets or pkg in sockets:
                    cores.setdefault((pkg, cid), []).append(cpu)
        return cores
    for q in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/core_id"):
        cpu = int(q.split("/")[5][3:])
        pkg = int(open(q.replace("core_id", "physical_package_id")).read())
        if not sockets or pkg in sockets:
            cores.setdefault((pkg, int(open(q).read())), []).append(cpu)
    return cores
