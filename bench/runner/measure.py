"""The one measurement: find this server's capacity on the agent mix and
derive its CPU-to-GPU ratio, in under two hours.

    python -m runner.measure [--force] [--steps N] [--hold S]

Steps (status in data/ui/status.json, read live by the page):

1. Preflight (runner.preflight): refuse a busy or undersized server unless --force.
2. Plan (runner.plan): core pools, instances and the predicted capacity rate.
3. Samplers for the whole run: per-core (mpstat), per-process (pidstat), with
   the topology and time zone they need.
4. Load steps: the fleet held at one arrival rate for HOLD seconds per step,
   judged as the R770 record was (backend.capacity.judge.plateau: completions
   keep pace with arrivals and the generator delivered its rate). The first
   step runs at the predicted rate, the next 8% above it if it kept up or 8%
   below if not, then the midpoint of the bracket, while the time budget lasts.
5. Analysis (runner.results): capacity, CPU breakdown, replay; the page rebuilds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time

import yaml

from runner import plan as planner
from runner import preflight
from runner.status import Status

R = os.environ.get("BENCH_ROOT", "/opt/bench")
KIT = os.environ.get("KIT_ROOT", "/opt/kit")
DATA = os.path.join(R, "data")
BUDGET_S = 2 * 3600                      # the seller's limit, start to results
HOLD_S = 1800                            # per load step, as the R770 record; judged over 600..1700 s.
#   A 1,500 s hold reads capacity low: at the R770's capacity rate the backlog
#   grew 45-67 workflows in 1,800 s plateaus (limit 82) but 73 in a 1,500 s
#   plateau (limit 68), because the judged cohort sits earlier in the transient.
STEP_OVERHEAD_S = 210                    # fleet start, health waits, drain, settle, cool-down
ANALYSIS_S = 300
STEP = 0.08                              # first move from the predicted rate
TILE_SIDS = {"task": "task_ticket", "research": "deep_research", "ingestion": "ingestion",
             "analyst": "analyst_large", "code": "code_agent"}
ENTERPRISE = {"task": 6, "research": 1, "ingestion": 1, "analyst": 2, "code": 2}


def load_calibration() -> tuple[dict, str]:
    cal = yaml.safe_load(open(os.path.join(KIT, "calibration.yaml")))
    shaping = {"model": cal["model"], "mix": cal["mix"]}
    digest = hashlib.sha256(json.dumps(shaping, sort_keys=True).encode()).hexdigest()[:12]
    return cal, digest


def calibration_env(cal: dict, run_dir: str) -> dict:
    m = cal["model"]
    env = {
        "CAPACITY_SERVING_PROFILE": os.path.join(KIT, m["profile"]),
        "CAPACITY_SERVING_CONCURRENCY": str(m.get("concurrency", 2)),
        "CAPACITY_MODEL_TTFT_MS": str(m["modeled"]["ttft_ms"]),
        "CAPACITY_MODEL_DECODE_TPS": str(m["modeled"]["decode_tokens_per_s"]),
        "CAPACITY_MODEL_PREFILL_TPS": str(m["modeled"]["prefill_tokens_per_s"]),
    }
    if m.get("timing", "recorded") == "modeled":
        env["CAPACITY_SERVING_TIMING"] = "modeled"
    tile = {k: int(v) for k, v in (cal["mix"].get("tile") or {}).items()}
    if tile == ENTERPRISE:
        env["CAPACITY_E2E_TILE"] = "enterprise"
    else:
        extra = os.path.join(run_dir, "tile.yaml")
        yaml.safe_dump({"e2e_tiles": {"kit": {TILE_SIDS[k]: n for k, n in tile.items() if n > 0}}}, open(extra, "w"))
        env["CAPACITY_SCENARIOS_EXTRA"] = extra
        env["CAPACITY_E2E_TILE"] = "kit"
    return env


def sh(cmd: str, env: dict | None = None, log: str | None = None, check: bool = True) -> int:
    out = open(log, "a") if log else None
    rc = subprocess.call(["bash", "-c", cmd], cwd=R, env={**os.environ, **(env or {})},
                         stdout=out or subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if check and rc != 0:
        raise RuntimeError(f"command failed ({rc}): {cmd}" + (f"; see {log}" if log else ""))
    return rc


def start_samplers(run_dir: str) -> list[subprocess.Popen]:
    # The samplers stamp their rows in UTC, and timezone.txt says so, so the
    # analysis lines them up with the ledgers whatever zone the host keeps.
    env = {**os.environ, "TZ": "UTC"}
    procs = [subprocess.Popen(["mpstat", "-P", "ALL", "10"], stdout=open(os.path.join(run_dir, "mpstat.log"), "w"),
                              stderr=subprocess.STDOUT, env=env),
             subprocess.Popen(["pidstat", "-u", "-h", "-l", "10"], stdout=open(os.path.join(run_dir, "pidstat.log"), "w"),
                              stderr=subprocess.STDOUT, env=env)]
    with open(os.path.join(run_dir, "topology.txt"), "w") as fh:
        for c in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*"), key=lambda p: int(p.rsplit("cpu", 1)[1])):
            t = c + "/topology/"
            fh.write(f"{c.rsplit('cpu', 1)[1]}:{open(t + 'thread_siblings_list').read().strip()} "
                     f"pkg={open(t + 'physical_package_id').read().strip()} core={open(t + 'core_id').read().strip()}\n")
    open(os.path.join(run_dir, "timezone.txt"), "w").write("UTC\n")
    return procs


def fresh_databases(k: int, rest_cpus: str, password: str) -> None:
    for c in subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split():
        if c.startswith("ratio-pg-f"):
            subprocess.call(["docker", "rm", "-f", c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for v in subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True, text=True).stdout.split():
        if v.startswith("ratio_pg_f"):
            subprocess.call(["docker", "volume", "rm", v], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    image = os.environ.get("POSTGRES_IMAGE", "postgres:16-alpine")
    for i in range(1, k + 1):
        r = subprocess.run(["docker", "run", "-d", "--name", f"ratio-pg-f{i}", "-p", f"127.0.0.1:{5440 + i}:5432",
                            "--cpuset-cpus", rest_cpus, "-e", "POSTGRES_USER=xeon", "-e", f"POSTGRES_PASSWORD={password}",
                            "-e", f"POSTGRES_DB=orchestrator_f{i}", "-v", f"ratio_pg_f{i}:/var/lib/postgresql/data",
                            image, "-c", "max_connections=300"], capture_output=True, text=True)
        if r.returncode != 0:   # never echo the command: it carries the database password
            raise RuntimeError(f"could not start database ratio-pg-f{i}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")


def archive_fleet_files(run_dir: str) -> None:
    """Earlier runs' ledgers in the fleet folders: the series script copies the
    newest one, so a failed start would pass old files off as this run's."""
    for d in glob.glob(os.path.join(DATA, "capacity", "fleet*")):
        old = glob.glob(os.path.join(d, "capacity-*")) + glob.glob(os.path.join(d, "evidence-*")) + glob.glob(os.path.join(d, "judgment-*"))
        if old:
            dest = os.path.join(run_dir, "previous-fleet-files", os.path.basename(d))
            os.makedirs(dest, exist_ok=True)
            for f in old:
                shutil.move(f, dest)


def judge(series_dir: str, rate: float, instances: int) -> dict:
    from backend.capacity.judge import plateau
    files = sorted(glob.glob(os.path.join(series_dir, f"rate-{rate}-i*-evidence-*.jsonl.gz")))
    caps = glob.glob(os.path.join(series_dir, f"rate-{rate}-i*-capacity-*.json"))
    if len(files) != instances or len(caps) != instances:
        return {"passed": False, "keeps_up": False, "generator_ok": False,
                "error": f"incomplete records: expected {instances} instances, found {len(files)} ledgers and {len(caps)} capacity reports"}
    j = plateau(files)
    j["passed"] = bool(j.get("keeps_up") and j.get("generator_ok"))
    return j


def next_rate(history: list[tuple[float, bool]], first: float) -> float:
    ok = [r for r, p in history if p]
    bad = [r for r, p in history if not p]
    lo, hi = (max(ok) if ok else None), (min(bad) if bad else None)
    if lo is not None and hi is not None:
        return (lo + hi) / 2
    if lo is not None:
        return lo * (1 + STEP)
    return hi * (1 - STEP)


def main() -> None:
    global _status
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true", help="run even if preflight fails")
    ap.add_argument("--hold", type=int, default=int(os.getenv("MEASURE_HOLD_S", HOLD_S)))
    ap.add_argument("--steps", type=int, default=0, help="number of load steps (default: as many as the budget allows)")
    ap.add_argument("--budget", type=int, default=int(os.getenv("MEASURE_BUDGET_S", BUDGET_S)))
    ap.add_argument("--analyse", metavar="RUN", help="redo the analysis of a finished run (data/capacity/runs/RUN), no new load")
    a = ap.parse_args()
    if a.analyse:
        run_dir = os.path.join(DATA, "capacity", "runs", a.analyse)
        meta = json.load(open(os.path.join(run_dir, "run.json")))
        steps = json.load(open(os.path.join(run_dir, "steps.json")))
        pf = json.load(open(os.path.join(run_dir, "preflight.json")))
        _status = Status(os.path.join(DATA, "ui", "status.json"), run=a.analyse, budget_s=0, steps=steps)
        finish(run_dir, steps, meta, pf, _status)
        return

    t0 = time.time()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    run_dir = os.path.join(DATA, "capacity", "runs", stamp)
    os.makedirs(run_dir, exist_ok=True)
    status = _status = Status(os.path.join(DATA, "ui", "status.json"), run=stamp, budget_s=a.budget)

    status.phase("preflight", "Checking the server")
    pf = preflight.check()
    json.dump(pf, open(os.path.join(run_dir, "preflight.json"), "w"), indent=1)
    status.update(preflight=pf)
    if not pf["ok"] and not a.force:
        status.phase("refused", pf["summary"])
        print(preflight.report(pf))
        sys.exit(2)

    cal, digest = load_calibration()
    p = planner.plan(planner.read_topology())
    env = {**calibration_env(cal, run_dir), "PLATEAU_HOLD": str(a.hold), "FLEET_RESET_DB": "0",
           "SCHEDULER_ENABLED": "0"}
    for line in planner.env(p).splitlines():
        for kv in line.replace("export ", "").split():
            k, v = kv.split("=", 1)
            env[k] = v
    password = secrets.token_urlsafe(18)
    with open(os.path.join(R, ".env.adl"), "w") as fh:
        fh.write(f"DATABASE_URL=postgresql+asyncpg://xeon:{password}@127.0.0.1:5441/orchestrator_f1\n"
                 f"MOCK_ROUTER_WORKERS={os.getenv('MOCK_ROUTER_WORKERS', '8')}\n")
    run_meta = {"run": stamp, "started": t0, "plan": p, "calibration": cal, "calibration_hash": digest,
                "hold_s": a.hold, "system": preflight.system_info()}
    json.dump(run_meta, open(os.path.join(run_dir, "run.json"), "w"), indent=1)
    status.update(plan=p, system=run_meta["system"], calibration_hash=digest)

    status.phase("start", "Starting databases, retrieval services and samplers")
    archive_fleet_files(run_dir)
    samplers = []
    log = os.path.join(run_dir, "measure.log")
    history: list[tuple[float, bool]] = []
    steps = []
    try:
        fresh_databases(p["instances"], p["cpusets"]["application"], password)
        samplers = start_samplers(run_dir)
        sh("scripts/provision_retrieval.sh", env, log)
        first = p["predicted_rate_per_instance"]
        rate = first
        n = 0
        while True:
            n += 1
            elapsed = time.time() - t0
            left = a.budget - elapsed - ANALYSIS_S
            if a.steps and n > a.steps:
                break
            if not a.steps and left < a.hold + STEP_OVERHEAD_S:
                break
            rate = round(rate, 4)
            status.step(n, rate, p["instances"], a.hold)
            seed = 20000 + n * 100
            archive_fleet_files(run_dir)
            sh(f"scripts/plateau_series.sh {seed} {rate}", {**env, "PLATEAU_RATE": ""}, log)
            series = max(glob.glob(os.path.join(DATA, "capacity", f"series-{seed}-*")), key=os.path.getmtime)
            dest = os.path.join(run_dir, f"step-{n}")
            shutil.move(series, dest)
            j = judge(dest, rate, p["instances"])
            history.append((rate, j["passed"]))
            steps.append({"step": n, "rate_per_instance": rate, "rate": round(rate * p["instances"], 3),
                          "dir": os.path.basename(dest), "passed": j["passed"],
                          "achieved_ratio": j.get("achieved_ratio"), "backlog_delta": j.get("backlog_delta"),
                          "resident": j.get("resident_measured"), "keeps_up": j.get("keeps_up"),
                          "generator_ok": j.get("generator_ok"), "error": j.get("error")})
            json.dump(steps, open(os.path.join(run_dir, "steps.json"), "w"), indent=1)
            status.step_done(steps[-1])
            rate = next_rate(history, first)
            time.sleep(60)          # drain: the last rung's jobs finish before the next starts
    finally:
        for s in samplers:
            s.terminate()
        sh("docker ps -q --filter name=^ratio-tei- --filter name=^ratio-pg- | xargs -r docker stop >/dev/null; true", check=False)
    finish(run_dir, steps, run_meta, pf, status)


def finish(run_dir: str, steps: list, run_meta: dict, pf: dict, status: Status) -> None:
    ok = [s for s in steps if s["passed"]]
    if not ok:
        status.phase("no-capacity", "No load step kept up; the server is below the lowest rate tried")
        sys.exit(3)
    best = max(ok, key=lambda s: s["rate_per_instance"])
    bracketed = any(not s["passed"] and s["rate_per_instance"] > best["rate_per_instance"] for s in steps)
    status.phase("analysis", f"Analysing step {best['step']}")
    from runner import results
    run_meta["memory_bound"] = pf.get("memory_bound")
    res = results.build(run_dir, best, run_meta)
    res["kit"]["steps"] = steps
    res["kit"]["bracketed"] = bracketed   # False: every step kept up, so capacity is at least this rate
    json.dump(res, open(os.path.join(run_dir, "results.json"), "w"), indent=1)
    shutil.copy(os.path.join(run_dir, "results.json"), os.path.join(DATA, "ui", "results.json"))
    rc = subprocess.call(["node", os.path.join(R, "ui", "replay", "build.cjs"), os.path.join(run_dir, "results.json"),
                          os.path.join(DATA, "ui", "replay.html"), os.path.join(KIT, "calibration", "reference-r770.json")],
                         stdout=open(os.path.join(run_dir, "replay-build.log"), "w"), stderr=subprocess.STDOUT)
    if rc != 0:
        print(f"replay page build failed; see {run_dir}/replay-build.log (results are still on the page)")
    status.phase("done", "Results ready", results=res)
    print(results.summary(res))


_status: Status | None = None

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - the page must show a failed run as failed
        if _status is not None:
            _status.phase("failed", f"The run stopped: {exc}")
        raise
