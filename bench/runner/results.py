"""This server's results from its capacity step, computed as the R770 results
of record were (see the published methodology):

- workflows/s: the instances' achieved arrival rates, summed
- busy cores: per-core samples over the judged window, each physical core
  counted as its busier hardware thread, averaged over time
- resident agents: the fleet's in-flight samples over the judged span
- memory: whole-host samples over the window, mean and maximum
- output tokens/s: the declared mix at the achieved rate, each agent type's
  output per workflow as served in this run plus its model judgments
- the CPU breakdown: service pools on their own cores, the agent runtime and
  supporting processes from the per-process samples, and the application
  pool's remaining time split across job steps by each job's own CPU record
- CPU work per workflow by agent type, from the same records (in the mix)
"""
from __future__ import annotations

import collections
import glob
import json
import math
import os
import statistics as st
import sys

KIT = os.environ.get("KIT_ROOT", "/opt/kit")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import cpu_breakdown  # noqa: E402
import mpstat_log  # noqa: E402
import pidstat_breakdown  # noqa: E402

NAMES = {"task_ticket": "Task", "deep_research": "Research", "ingestion": "Ingestion",
         "analyst_large": "Analyst", "code_agent": "Code"}
KEYS = {v.lower(): k for k, v in NAMES.items()}
STEPS = [  # (job step key, group, piece, share of the key's time)
    ("large_rerun", "Data analysis", "Compare two periods (two passes)", 1.0),
    ("large", "Data analysis", "Profile events", 0.5),
    ("large", "Data analysis", "Prepare report data", 0.5),
    ("ci", "Builds and tests", "CI: sanitizer build, static analysis, tests", 1.0),
    ("verify", "Builds and tests", "Verify: rebuild changed files, rerun checks", 1.0),
    ("setup", "Builds and tests", "Setup: build Lua and SQLite, run test suites", 1.0),
    ("scan", "Document intake", "OCR intake: render, OCR, redact, chunk", 1.0),
    ("fetch", "Research", "Fetch and parse sources", 1.0),
]
RUNTIME = ("instance executors", "instance control processes")
SUPPORT = ("databases (PostgreSQL)", "container runtime", "docker port proxies", "kernel threads", "systemd and journald")


def _cpuset(s: str) -> set[int]:
    return cpu_breakdown.cpuset(s)


def ratio_label(gpus_per_socket: float) -> str:
    """Nearest small whole-number CPU:GPU ratio (sockets to GPUs). Under a
    quarter of a GPU per socket it reads as sockets per GPU (for example 25:1)."""
    if gpus_per_socket <= 0:
        return "-"
    if gpus_per_socket < 0.25:
        return f"{round(1 / gpus_per_socket)}:1"
    best = min(((c, g) for c in range(1, 5) for g in range(1, 9)),
               key=lambda cg: (abs(cg[1] / cg[0] - gpus_per_socket), cg[0] + cg[1]))
    return f"{best[0]}:{best[1]}"


def judge_tokens(profile: str) -> dict[str, float]:
    acc = collections.defaultdict(list)
    for line in open(profile):
        r = json.loads(line)
        if r.get("ok") and r.get("role") == "judge":
            acc[r["archetype"]].append(int(r.get("completion_tokens") or 0))
    return {k: st.fmean(v) for k, v in acc.items()}


def _log(run_dir: str, name: str) -> str:
    p = os.path.join(run_dir, name)
    return p if os.path.exists(p) else p + ".gz"


def build(run_dir: str, best: dict, meta: dict) -> dict:
    step_dir = os.path.join(run_dir, best["dir"])
    rate = best["rate_per_instance"]
    hold = meta["hold_s"]
    # The record's window: 600 s after the step starts to 100 s before it ends.
    # (A short test hold scales the start so the window is not empty.)
    lo, hi = (600.0 if hold >= 1500 else round(hold * 0.4)), float(hold - 100)
    cal, plan, system = meta["calibration"], meta["plan"], meta["system"]
    log = _log(run_dir, "mpstat.log")

    caps = sorted(glob.glob(os.path.join(step_dir, f"rate-{rate}-i*-capacity-*.json")))
    evs = sorted(glob.glob(os.path.join(step_dir, f"rate-{rate}-i*-evidence-*.jsonl.gz")))
    achieved = sum(json.load(open(c))["sustainable_capacity"]["levels"][0]["achieved_rate"] for c in caps)

    # Busy cores and pools (cpu_breakdown.plateau reads the step's ledgers and the per-core samples).
    mp = mpstat_log.samples(log)
    cores = mpstat_log.topology(log)
    pools = {"reranker": _cpuset(plan["cpusets"]["reranker"]), "query embedder": _cpuset(plan["cpusets"]["query_embedder"]),
             "ingest embedder": _cpuset(plan["cpusets"]["document_embedder"])}
    cb = cpu_breakdown.plateau(step_dir, str(rate), lo, hi, mp, cores, pools)
    busy_cores, host_threads = cb["host_cores"], cb["host_threads"]

    # Memory: whole-host samples (every instance samples the same host; read one).
    mem, t0 = [], None
    import gzip
    for line in gzip.open(evs[0], "rt"):
        e = json.loads(line)
        if e.get("k") == "header":
            t0 = e["started_at"]
        elif e.get("k") == "sample" and t0 and lo <= e["ts"] - t0 <= hi and e.get("mem_gb") is not None:
            mem.append(e["mem_gb"])

    # Model demand per workflow: served in this run, plus judgments.
    work = json.load(open(os.path.join(KIT, "calibration", "profile-gpt-oss-20b.workload.json")))
    jcalls = work["judge_calls_per_workflow"]
    jtok = judge_tokens(os.path.join(KIT, cal["model"]["profile"]))
    served = collections.defaultdict(lambda: [0, 0])
    for c in caps:
        for sid, row in (json.load(open(c)).get("per_scenario") or {}).items():
            served[sid][0] += int(row.get("tokens_out") or 0)
            served[sid][1] += int(row.get("calls") or 0)
    calls_wf = collections.defaultdict(list)
    lookups = collections.defaultdict(lambda: collections.defaultdict(list))   # sid -> stage -> calls per workflow
    for f in evs:
        for line in gzip.open(f, "rt"):
            e = json.loads(line)
            stg = e.get("st") or {}
            if e.get("k") == "unit" and e.get("ok") and "model_wait_ms" in stg:
                calls_wf[e["sid"]].append(stg["model_wait_ms"][1])
                for k in ("rerank_call_ms", "embed_call_ms"):
                    lookups[e["sid"]][k].append(stg.get(k, [0, 0])[1])
    tile = {KEYS[k]: int(v) for k, v in cal["mix"]["tile"].items() if int(v) > 0}
    weight = sum(tile.values())
    out_wf = {sid: (served[sid][0] / served[sid][1] if served[sid][1] else 0.0) + jcalls.get(sid, 0) * jtok.get(sid, 0.0)
              for sid in tile}
    model_calls = {sid: (st.fmean(calls_wf[sid]) if calls_wf[sid] else 0.0) + jcalls.get(sid, 0) for sid in tile}
    tok_s = achieved * sum(tile[s] * out_wf[s] for s in tile) / weight
    calls_s = achieved * sum(tile[s] * model_calls[s] for s in tile) / weight

    # GPUs: arithmetic on calibration.yaml (the page redoes it live).
    g = cal["gpu"]
    gpus = tok_s / g["output_tokens_per_s_per_gpu"]
    sockets = max(1, system["sockets"])

    # CPU breakdown, in busy hardware threads.
    rows = [r for r in pidstat_breakdown.parse(_log(run_dir, "pidstat.log")) if t0 + lo <= r[0] <= t0 + hi]
    n_ps = len({r[0] for r in rows}) or 1
    by = collections.defaultdict(float)
    for _, cpu, cmd in rows:
        by[pidstat_breakdown.group_of(cmd)] += cpu / 100.0 / n_ps
    runtime = sum(by.get(k, 0.0) for k in RUNTIME)
    support = sum(by.get(k, 0.0) for k in SUPPORT)
    services = {k: v["threads_busy"] for k, v in cb["pools"].items()}
    app = cb["app_pool"]["threads_busy"]
    jobs = cb["job_threads_by_step"]
    job_total = sum(jobs.values()) or 1.0
    job_pool = max(0.0, app - runtime - support)
    breakdown = []
    for key, group, piece, part in STEPS:
        if jobs.get(key):
            breakdown.append({"group": group, "piece": piece, "share": job_pool * jobs[key] / job_total * part / host_threads * 100})
    breakdown += [
        {"group": "Document intake", "piece": "Document embedding service", "share": services["ingest embedder"] / host_threads * 100},
        {"group": "Retrieval", "piece": "Reranking service", "share": services["reranker"] / host_threads * 100},
        {"group": "Retrieval", "piece": "Query embedding service", "share": services["query embedder"] / host_threads * 100},
        {"group": "Agent runtime and services", "piece": "Executors and control processes", "share": runtime / host_threads * 100},
        {"group": "Agent runtime and services", "piece": "Databases, container runtime, kernel", "share": support / host_threads * 100},
    ]
    tot = sum(b["share"] for b in breakdown) or 1.0
    for b in breakdown:                       # close to 100 (sampling rounding)
        b["share"] = round(b["share"] * 100 / tot, 1)
    shares = collections.defaultdict(float)
    for b in breakdown:
        fam = ("execution" if b["piece"].startswith("Executors") else "support" if b["piece"].startswith("Databases")
               else "retrieval" if "service" in b["piece"] else "sandbox")
        shares[fam] += b["share"]

    # CPU work per workflow by agent type, in the mix, from the same records:
    # the application pool's job time by each agent's own job records; the
    # reranker and query embedder by each agent's calls to them; the document
    # embedder to ingestion; the runtime and supporting processes by model
    # calls. Hardware-thread time converts to physical cores at the host's
    # measured ratio (busy cores over busy threads).
    per = cb["per_workflow"]
    arr = {s: achieved * tile[s] / weight for s in tile}
    mean = lambda xs: st.fmean(xs) if xs else 0.0
    job_wf = {s: sum((per.get(s, {}).get("job_cpu_s_per_workflow") or {}).values()) for s in tile}
    job_rate = sum(arr[s] * job_wf[s] for s in tile) or 1.0

    def by_use(threads: float, use: dict) -> dict:
        rate_ = sum(arr[s] * use[s] for s in tile)
        return {s: (threads * use[s] / rate_ if rate_ else 0.0) for s in tile}
    parts = [
        {s: job_pool * job_wf[s] / job_rate for s in tile},
        by_use(services["reranker"], {s: mean(lookups[s]["rerank_call_ms"]) for s in tile}),
        by_use(services["query embedder"], {s: mean(lookups[s]["embed_call_ms"]) for s in tile}),
        by_use(services["ingest embedder"], {s: 1.0 if s == "ingestion" else 0.0 for s in tile}),
        by_use(runtime + support, model_calls),
    ]
    thread_s = {s: sum(p_[s] for p_ in parts) for s in tile}
    per_core = busy_cores / host_threads
    core_s = {s: thread_s[s] * per_core for s in tile}

    # For the replay page: each agent's mean workflow lifetime at capacity
    # (workflows submitted 300 s or more into the step and finished before it
    # ended), and the host's CPU and memory over the judged window as factors
    # of their means, starting where CPU sits at its mean.
    settle = 300 if hold >= 1500 else hold * 0.2     # a short test hold scales the offset
    life = collections.defaultdict(list)
    end_at = None
    for f in evs:
        start = None
        for line in gzip.open(f, "rt"):
            e = json.loads(line)
            if e.get("k") == "header":
                start = e["started_at"]
            elif e.get("k") == "footer":
                end_at = max(end_at or 0, e.get("ended_at") or 0)
            elif e.get("k") == "unit" and e.get("ok") and e.get("end") and start and e["sub"] >= start + settle:
                life[e["sid"]].append((e["sub"], e["end"]))
    timings = []
    for s in tile:
        rows = [b - a for a, b in life[s] if not end_at or b <= end_at]
        timings.append({"name": NAMES[s], "samples": len(rows), "meanSeconds": round(st.fmean(rows), 1) if rows else None})
    allcpu = sorted({c for cs in cores.values() for c in cs})
    win = [(ts, sum(v.get(c, 0.0) for c in allcpu)) for ts, v in sorted(mp.items()) if t0 + lo <= ts <= t0 + hi]
    memw = []
    for line in gzip.open(evs[0], "rt"):
        e = json.loads(line)
        if e.get("k") == "sample" and lo <= e["ts"] - t0 <= hi and e.get("mem_gb"):
            memw.append((e["ts"], e["mem_gb"]))
    trace = []
    if win and memw:
        mc, mm = st.fmean(v for _, v in win), st.fmean(v for _, v in memw)
        raw = []
        for ts, v in win:
            m = min(memw, key=lambda x: abs(x[0] - ts))[1]
            raw.append([ts, v / mc, m / mm])
        k = min(range(len(raw)), key=lambda i: abs(raw[i][1] - 1))
        step_s = (raw[-1][0] - raw[0][0]) / max(1, len(raw) - 1)
        trace = [[round(i * step_s, 1), round(r[1], 3), round(r[2], 3)] for i, r in enumerate(raw[k:] + raw[:k])]

    res = {
        "kit": {"run": meta["run"], "step": best["step"], "rate_per_instance": rate, "instances": plan["instances"],
                "hold_s": hold, "window_s": [lo, hi], "calibration_hash": meta["calibration_hash"],
                "memory_bound": bool(meta.get("memory_bound"))},
        "platform": {"system": system["system"], "processors": f"{system['sockets']} x {system['processor']}" if system.get("processor") else "",
                     "sockets": system["sockets"], "cores": system["cores"], "threads": system["threads"],
                     "memoryGB": system["memory_gb"],
                     "allocation": {"reranking": plan["per_socket"] and sum(s["reranker"] for s in plan["per_socket"].values()),
                                    "queryEmbedding": sum(s["query_embedder"] for s in plan["per_socket"].values()),
                                    "ingestEmbedding": sum(s["document_embedder"] for s in plan["per_socket"].values()),
                                    "application": plan["application_cores"]}},
        "capacity": {"workflowsPerSecond": round(achieved, 3), "busyCores": round(busy_cores, 1),
                     "cpuPercent": round(100 * busy_cores / system["cores"]), "resident": best.get("resident"),
                     "memoryGB": round(st.fmean(mem)) if mem else None, "memoryPeakGB": round(max(mem)) if mem else None,
                     "callsPerSecond": round(calls_s, 1), "outputTokensPerSecond": round(tok_s, -1),
                     "coreMsPerSecond": round(busy_cores * 1000), "coreMsPerOutputToken": round(busy_cores * 1000 / tok_s, 1),
                     "busyThreads": round(host_threads), "cpuShares": {k: round(v, 1) for k, v in shares.items()},
                     "cpuBreakdown": breakdown},
        "serving": {"model": cal["model"]["name"], "gpu": g["name"], "gpuServer": g["server"],
                    "gpusPerServer": g["gpus_per_server"], "outputTokensPerSecondPerGpu": g["output_tokens_per_s_per_gpu"]},
        "ratio": {"gpusPerServer": round(gpus, 2), "gpusPerSocket": round(gpus / sockets, 2),
                  "cpuToGpu": ratio_label(gpus / sockets), "serversPerGpuServer": round(g["gpus_per_server"] / gpus, 1),
                  "gpusAllocated": math.ceil(gpus - 1e-9)},
        "archetypes": [{"id": s, "name": NAMES[s], "arrivals": tile[s], "coreMs": round(core_s[s] * 1000),
                        "modelCalls": round(model_calls[s], 1), "outputTokens": round(out_wf[s])} for s in tile],
        "definitions": {"tile": {NAMES[s]: tile[s] for s in tile}, "mix": cal["mix"]["name"]},
        "replay": {"timings": timings, "trace": trace},
    }
    return res


def summary(r: dict) -> str:
    c, q, p = r["capacity"], r["ratio"], r["platform"]
    return "\n".join([
        f"{p['system']}: {p['processors']}, {p['cores']} cores, {p['memoryGB']} GB",
        f"  capacity      {c['workflowsPerSecond']} workflows/s, {c['resident']} agents resident, "
        f"{c['busyCores']} of {p['cores']} cores busy ({c['cpuPercent']}%)",
        f"  memory        {c['memoryGB']} GB mean, {c['memoryPeakGB']} GB peak",
        f"  model demand  {c['outputTokensPerSecond']:,.0f} output tokens/s, {c['callsPerSecond']} calls/s",
        f"  ratio         {q['gpusPerServer']} GPUs per server ({q['cpuToGpu']} CPU:GPU), "
        f"{q['serversPerGpuServer']} servers per {r['serving']['gpuServer']}",
        "",
        f"Open the page through the tunnel to see the replay: http://127.0.0.1:{os.environ.get('UI_PORT', '8766')}",
    ])
