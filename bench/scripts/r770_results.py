"""Whole-server results of record for a two-socket PowerEdge R770 plateau set.

    python3 scripts/r770_results.py <set-dir> <mpstat-log> [rate]

For the capacity rate (default: the highest rate every series keeps up,
read from the set summary) this prints, per R770:

- achieved workflows/s (sum over the fleet's instances), busy physical
  cores (mean over +600 to +1700 s of each plateau, the busier sibling per
  core, as scripts/core_windows.py) and CPU utilization over all cores
- output tokens/s the agents need: achieved rate x the declared mix's
  output per workflow (OUT_PER_WF, from the serving profile, as
  scripts/ratio_from_profile.py), model calls/s (CALLS_PER_TILE per tile)
- GPUs per R770 at the GPU serving rate of record, R770s per XE7740
- resident agents by Little's law (the set summary's figure)
- memory: mean and maximum whole-host sample over the same window
- CPU work by family (each instance's sampler, normalized), the sandboxed
  share split by job type from each workflow's recorded job CPU time
- CPU work per workflow by agent type: each workflow's recorded job CPU,
  plus the non-job CPU (runtime, retrieval, services) shared by model
  calls, scaled so the agents' total equals the measured busy core time
"""
from __future__ import annotations

import collections
import glob
import gzip
import json
import os
import re
import statistics as st
import subprocess
import sys

SET, LOG = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mpstat_log  # noqa: E402
NCORES = len(mpstat_log.topology(LOG))   # physical cores: the run's topology.txt, else this machine's
LO, HI = 600, 1700
OUT_PER_WF = 6710           # declared mix output tokens per workflow (gptoss20b-low-faithful-v23)
CALLS_PER_TILE = 119        # model calls per 12-workflow tile, judgments included
TILE = {"task_ticket": 6, "code_agent": 2, "analyst_large": 2, "deep_research": 1, "ingestion": 1}
CALLS = {"task_ticket": 4, "deep_research": 20, "ingestion": 7, "analyst_large": 17, "code_agent": 17}
GPU_RATE = 4020             # output tokens/s per RTX PRO 6000 at the set point of record
GPUS_PER_XE = 8
JOB_TYPE = {"scan": "Document OCR and parsing", "setup": "Builds and tests", "ci": "Builds and tests", "verify": "Builds and tests",
            "large": "Data analysis", "large_rerun": "Data analysis", "fetch": "Research fetch"}

summary = json.load(open(os.path.join(SET, "summary.json")))
series = [l.strip() for l in open(os.path.join(SET, "series-dirs.txt")) if l.strip()]
rate = sys.argv[3] if len(sys.argv) > 3 else None
if rate is None:
    md = open(os.path.join(SET, "summary.md")).read()
    keeps = [r for r, k in re.findall(r"^\| ([\d.]+) \| [^|]+\| [^|]+\| (yes|NO) \|", md, re.M) if k == "yes"]
    rate = keeps[-1]
little = re.search(rf"^\| {re.escape(rate)} \|(?:[^|]*\|){{10}} (\d+) \(", open(os.path.join(SET, "summary.md")).read(), re.M)

achieved, cores, fam, mem, jobs_cpu, units_by = [], [], collections.defaultdict(float), [], collections.defaultdict(float), collections.defaultdict(list)
for s in series:
    caps = sorted(glob.glob(os.path.join(s, f"rate-{rate}-i*-capacity-*.json")))
    achieved.append(sum(json.load(open(c))["sustainable_capacity"]["levels"][0]["achieved_rate"] for c in caps))
    out = subprocess.run([sys.executable, "scripts/core_windows.py", s, LOG], capture_output=True, text=True,
                         env=dict(os.environ, WINDOW_LO=str(LO), WINDOW_HI=str(HI))).stdout
    m = re.search(rf"rate {re.escape(rate)}/s: .*?\(([\d.]+) cores", out)
    cores.append(float(m.group(1)))
    for ev in sorted(glob.glob(os.path.join(s, f"rate-{rate}-i*-evidence-*.jsonl.gz"))):
        t0 = None
        for line in gzip.open(ev, "rt"):
            e = json.loads(line)
            if e.get("k") == "header": t0 = e["started_at"]
            elif e.get("k") == "sample" and t0 and LO <= e["ts"] - t0 <= HI:
                if e.get("cpu_by"):
                    for k, v in e["cpu_by"].items(): fam[k] += v or 0
                if e.get("mem_gb") is not None and ev.endswith(".gz") and "-i1-" in ev: mem.append(e["mem_gb"])
            elif e.get("k") == "unit" and e.get("ok"):
                cpu = 0.0
                for k, (v, n) in (e.get("st") or {}).items():
                    mm = re.match(r"sandbox_(.+)_cpu_ms$", k)
                    if mm:
                        cpu += v; jobs_cpu[JOB_TYPE.get(mm.group(1), "Other jobs")] += v
                units_by[e["sid"]].append(cpu)

R = st.fmean(achieved); C = st.fmean(cores)
tok = R * OUT_PER_WF; calls = R / 12 * CALLS_PER_TILE; gpus = tok / GPU_RATE
tot = sum(fam.values()) or 1
share = {k: 100 * v / tot for k, v in fam.items()}
runtime = share.get("siblings", 0) + share.get("executors", 0) + share.get("control", 0)
support = share.get("database", 0) + share.get("other", 0) + share.get("mock_router", 0)
sandbox = share.get("sandbox", 0); retrieval = share.get("retrieval", 0)
jt = sum(jobs_cpu.values()) or 1
core_ms_s = C * 1000
job_mean = {a: st.fmean(v) for a, v in units_by.items() if v}
arr = {a: R * n / 12 for a, n in TILE.items()}
non_job = core_ms_s * (1 - sandbox / 100)
call_rate = sum(arr[a] * CALLS[a] for a in TILE)
per_wf = {a: job_mean.get(a, 0) + non_job * CALLS[a] / call_rate for a in TILE}
scale = core_ms_s / sum(arr[a] * per_wf[a] for a in TILE)
per_wf = {a: v * scale for a, v in per_wf.items()}
cpu_share = {a: 100 * arr[a] * per_wf[a] / core_ms_s for a in TILE}

res = {
 "rate_per_instance": float(rate), "series": [os.path.basename(s) for s in series],
 "workflows_per_s": round(R, 3), "busy_cores": round(C, 1), "cores": NCORES, "cpu_percent": round(100 * C / NCORES, 1),
 "core_ms_per_s": round(core_ms_s), "output_tokens_per_s": round(tok), "model_calls_per_s": round(calls, 2),
 "core_ms_per_token": round(core_ms_s / tok, 2), "core_s_per_call": round(core_ms_s / 1000 / calls, 2), "core_s_per_workflow": round(C / R, 1),
 "gpus_per_r770": round(gpus, 2), "gpus_per_cpu": round(gpus / 2, 2), "r770_per_xe7740": round(GPUS_PER_XE / gpus, 2),
 "resident_little": int(little.group(1)) if little else None,
 "memory_gb_mean": round(st.fmean(mem), 1) if mem else None, "memory_gb_max": round(max(mem), 1) if mem else None,
 "cpu_families_percent": {"sandbox": round(sandbox, 1), "runtime": round(runtime, 1), "retrieval": round(retrieval, 1), "support": round(support, 1)},
 "sandbox_by_type_percent_of_cpu": {k: round(sandbox * v / jt, 1) for k, v in sorted(jobs_cpu.items(), key=lambda x: -x[1])},
 "job_cpu_ms_per_workflow": {a: round(v) for a, v in job_mean.items()},
 "core_ms_per_workflow": {a: round(v, -2) for a, v in per_wf.items()},
 "core_s_per_call_by_agent": {a: round(per_wf[a] / 1000 / CALLS[a], 2) for a in TILE},
 "cpu_share_by_agent_percent": {a: round(v, 1) for a, v in cpu_share.items()},
 "check_job_cpu_vs_sampler": round(sum(arr[a] * job_mean.get(a, 0) for a in TILE) / core_ms_s * 100, 1),
}
print(json.dumps(res, indent=1))
