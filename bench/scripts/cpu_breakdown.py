"""Where the CPU time goes, from three independent records, without the
sampler's process families.

    python3 scripts/cpu_breakdown.py <mpstat-log> <series-dir>:<rate>[:<lo>:<hi>] ...

For each plateau (window lo..hi seconds after its start, default 600..1700):

1. Per-core samples (mpstat, 10 s): busy hardware threads over the whole
   host and over each core pool of the allocation (reranker, query
   embedder, ingest embedder, application pool), and busy physical cores
   (a core counted as its busier thread).
2. Per-job records: every workflow's sandboxed steps report their own CPU
   time (the job process plus its reaped children, measured inside the
   job), by step. Arrival rate x mean CPU per workflow gives each step's
   hardware threads.
3. The remainder: application-pool threads minus the jobs' threads, which
   holds the agent runtime, the databases, the model stand-in, job start
   and clean-up outside the job's own measurement, and the kernel.

The sampler's families are not used: in a fleet each instance scans the
other instances' process trees every 30 s, so a long-running job is
charged while it runs and again, through its parent's reaped-children
time, when it ends.
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpstat_log  # noqa: E402

STEP = {  # sandbox step key -> (activity, step label)
    "large": ("Data analysis", "analysis job over 100M events"),
    "large_rerun": ("Data analysis", "comparison over two periods"),
    "setup": ("Builds and tests", "setup: build Lua and SQLite, run tests"),
    "ci": ("Builds and tests", "CI: sanitizer build, static analysis, tests"),
    "verify": ("Builds and tests", "verify: rebuild changed files, rerun"),
    "scan": ("Document intake", "OCR intake: render, OCR, redact, chunk"),
    "fetch": ("Research", "fetch and parse sources"),
}


def cpuset(s):
    out = set()
    for part in (s or "").split(","):
        if "-" in part:
            a, b = part.split("-"); out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out


def pools(run_txt):
    t = open(run_txt).read()
    get = lambda k: cpuset(re.search(rf"^{k}=(\S*)", t, re.M).group(1))
    return {"reranker": get("rerank"), "query embedder": get("embed"), "ingest embedder": get("ingest")}


def plateau(series, rate, lo, hi, mp, cores, pl):
    files = sorted(glob.glob(os.path.join(series, f"rate-{rate}-i*-evidence-*.jsonl.gz")))
    t0 = None
    arrivals, units = 0, []
    for f in files:
        start = None
        for line in gzip.open(f, "rt"):
            e = json.loads(line)
            if e.get("k") == "header":
                start = e["started_at"]; t0 = start if t0 is None else min(t0, start)
            elif e.get("k") == "unit" and e.get("r") is not None and start is not None:
                rel = e["sub"] - start
                if lo <= rel <= hi:
                    arrivals += 1
                if e.get("ok") and lo - 900 <= rel <= hi:   # cohort for per-workflow means
                    units.append(e)
    span = hi - lo
    lam = arrivals / span
    # per-core samples in the window
    ts = [t for t in sorted(mp) if t0 + lo <= t <= t0 + hi]
    allcpu = sorted({c for cs in cores.values() for c in cs})
    thread = lambda cpus: st.fmean(sum(mp[t].get(c, 0.0) for c in cpus) / 100 for t in ts)
    core_busy = lambda keys: st.fmean(sum(max(mp[t].get(c, 0.0) for c in cores[k]) for k in keys) / 100 for t in ts)
    pool_keys = {name: [k for k, cs in cores.items() if set(cs) & cpus] for name, cpus in pl.items()}
    tier_keys = {k for ks in pool_keys.values() for k in ks}
    app_keys = [k for k in cores if k not in tier_keys]
    out = {"rate": rate, "arrivals_per_s": round(lam, 3), "samples": len(ts),
           "host_threads": round(thread(allcpu), 1), "host_cores": round(core_busy(list(cores)), 1),
           "pools": {name: {"cores": len(ks), "threads_busy": round(thread([c for k in ks for c in cores[k]]), 2),
                            "cores_busy": round(core_busy(ks), 2)} for name, ks in pool_keys.items()},
           "app_pool": {"cores": len(app_keys), "threads_busy": round(thread([c for k in app_keys for c in cores[k]]), 1),
                        "cores_busy": round(core_busy(app_keys), 1)}}
    # per-job CPU by step: arrivals of each type x mean CPU per workflow of that type
    by_sid = collections.defaultdict(list)
    for u in units:
        by_sid[u["sid"]].append(u)
    mix = collections.Counter()
    for f in files:
        start = None
        for line in gzip.open(f, "rt"):
            e = json.loads(line)
            if e.get("k") == "header":
                start = e["started_at"]
            elif e.get("k") == "unit" and e.get("r") is not None and start is not None and lo <= e["sub"] - start <= hi:
                mix[e["sid"]] += 1
    steps = collections.defaultdict(float)
    per_wf = {}
    for sid, us in by_sid.items():
        rate_sid = mix[sid] / span
        acc = collections.defaultdict(float)
        for u in us:
            for k, (v, n) in (u.get("st") or {}).items():
                m = re.match(r"sandbox_(.+)_cpu_ms$", k)
                if m:
                    acc[m.group(1)] += v / 1000
        per_wf[sid] = {"workflows_per_s": round(rate_sid, 4), "completed_in_cohort": len(us),
                       "job_cpu_s_per_workflow": {k: round(v / len(us), 1) for k, v in acc.items()}}
        for k, v in acc.items():
            steps[k] += rate_sid * v / len(us)
    out["per_workflow"] = per_wf
    out["job_threads_by_step"] = {k: round(v, 2) for k, v in sorted(steps.items(), key=lambda x: -x[1])}
    jobs = sum(steps.values())
    out["job_threads"] = round(jobs, 1)
    out["app_remainder_threads"] = round(out["app_pool"]["threads_busy"] - jobs, 1)
    return out


def main():
    mp = mpstat_log.samples(sys.argv[1])      # UTC epochs, the run's own zone
    cores = mpstat_log.topology(sys.argv[1])  # the run's topology.txt, else this machine's
    results = []
    for arg in sys.argv[2:]:
        parts = arg.split(":")
        series, rate = parts[0], parts[1]
        lo, hi = (float(parts[2]), float(parts[3])) if len(parts) > 3 else (600.0, 1700.0)
        run_txt = glob.glob(os.path.join(os.path.dirname(sys.argv[1]), "run.txt"))[0]
        results.append({"series": os.path.basename(series), **plateau(series, rate, lo, hi, mp, cores, pools(run_txt))})
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
