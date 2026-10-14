"""Busy hardware threads by process, from a `pidstat -u -h -l 10` log, over one plateau window.

    python3 scripts/pidstat_breakdown.py <pidstat.log> <series-dir> <rate> [lo hi]

Each sample row is one process's CPU over the last 10 s, in percent of one
hardware thread. Rows are grouped by what the process is (sandboxed job,
executor, database, model stand-in, retrieval service, kernel thread...),
averaged over the window (lo..hi seconds after the plateau starts, default
600..1400) and printed as busy threads and as a share of the host.

A process that starts and exits between two samples is not seen; the
sandboxed jobs' own records cover those (scripts/cpu_breakdown.py).
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpstat_log  # noqa: E402

GROUPS = [  # (group, pattern on the command line), first match wins
    ("sandbox job: data analysis", r"sandbox_job\.py"),
    ("sandbox job: build steps (job script)", r"sandbox_build_job\.py"),
    ("sandbox job: build steps (compilers, make, tests)", r"(^|/)(cc1|cc1plus|gcc|g\+\+|as|ld|collect2|make|lua|sqlite3|clang[^ ]*|cppcheck)( |$)|/src/lua |testfixture"),
    ("sandbox job: OCR intake (job script)", r"sandbox_scan_job\.py"),
    ("sandbox job: OCR intake (tesseract, renderers)", r"tesseract|pdftoppm|pdfium"),
    ("sandbox job: research fetch", r"sandbox_fetch_job\.py"),
    ("sandbox job: other job scripts", r"sandbox_[a-z_]*job\.py"),
    ("job launch wrappers (sudo, unshare, env)", r"^(sudo|unshare|env)( |$)|^/usr/bin/(sudo|unshare|env)"),
    ("model stand-in", r"mockrouter|mock_router"),
    ("instance executors", r"backend\.main:app .*--port (9[3-9]|1[01])\d\d"),
    ("instance control processes", r"backend\.main:app"),
    ("databases (PostgreSQL)", r"postgres"),
    ("docker port proxies", r"docker-proxy"),
    ("container runtime", r"dockerd|containerd"),
    ("reranking service", r"ort_rerank_server"),
    ("embedding services (TEI)", r"text-embeddings"),
    ("samplers (pidstat, mpstat)", r"pidstat|mpstat"),
    ("benchmark scripts (bash, curl, python helpers)", r"plateau_series|fleet_capacity|r770_run|^bash|^/bin/bash|curl|^python3 -c|^python3 -"),
    ("kernel threads", r"^\[?(kswapd|kcompactd|khugepaged|kworker|ksoftirqd|rcu|migration|kthreadd|watchdog|cpuhp|irq/|jbd2|xfs|nvme)"),
    ("systemd and journald", r"systemd|journald"),
]


def parse(path):
    """-> [(utc epoch, %cpu, command)]. pidstat prints the host's local time;
    the zone is read as scripts/mpstat_log.py does (MPSTAT_TZ, a timezone.txt
    beside the log, else this machine's), DST-aware, 12- or 24-hour stamps,
    advancing the date when the clock wraps past midnight."""
    tz = mpstat_log.zone(path)
    rows, day, last = [], None, None
    for line in (gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")):
        if line.startswith("Linux"):
            m = re.search(r"(\d\d)/(\d\d)/(\d{2,4})|(\d{4})-(\d\d)-(\d\d)", line)
            if m and m.group(1):
                y = int(m.group(3)); y += 2000 if y < 100 else 0
                day = dt.date(y, int(m.group(1)), int(m.group(2)))
            elif m:
                day = dt.date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            last = None
            continue
        m = re.match(r"(\d\d):(\d\d):(\d\d)(?: (AM|PM))?\s+\d+\s+\d+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s+\d+\s+(.*)$", line)
        if not m or day is None:
            continue
        hh, mm, ss, ap, cpu, cmd = m.groups()
        h = int(hh) % 12 + (12 if ap == "PM" else 0) if ap else int(hh)
        clock = h * 3600 + int(mm) * 60 + int(ss)
        if last is not None and clock < last - 12 * 3600:
            day += dt.timedelta(days=1)
        last = clock
        ts = int(dt.datetime(day.year, day.month, day.day, h, int(mm), int(ss), tzinfo=tz).timestamp())
        rows.append((ts, float(cpu), cmd.strip()))
    return rows


def group_of(cmd):
    for g, pat in GROUPS:
        if re.search(pat, cmd):
            return g
    return "other: " + cmd.split()[0].rsplit("/", 1)[-1][:40]


def main():
    log, series, rate = sys.argv[1], sys.argv[2], sys.argv[3]
    lo, hi = (float(sys.argv[4]), float(sys.argv[5])) if len(sys.argv) > 5 else (600.0, 1400.0)
    t0 = min(json.loads(next(gzip.open(f, "rt")))["started_at"] for f in glob.glob(f"{series}/rate-{rate}-i*-evidence-*.jsonl.gz"))
    rows = [r for r in parse(log) if t0 + lo <= r[0] <= t0 + hi]
    stamps = sorted({r[0] for r in rows})
    by = collections.defaultdict(float)
    for ts, cpu, cmd in rows:
        by[group_of(cmd)] += cpu / 100.0
    n = len(stamps)
    total = sum(by.values()) / n
    print(f"{n} samples, {total:.1f} busy threads seen by pidstat")
    for g, v in sorted(by.items(), key=lambda x: -x[1]):
        if v / n >= 0.05:
            print(f"  {v / n:7.2f} threads  {100 * v / n / total:5.1f}%  {g}")


if __name__ == "__main__":
    main()
