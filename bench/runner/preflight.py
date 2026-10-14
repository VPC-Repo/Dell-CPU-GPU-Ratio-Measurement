"""Is this server ready to measure, and what is it?

    python -m runner.preflight [--quick]

Checks (any FAIL refuses a run unless ./measure --force):
- at least 32 physical cores (64 or more recommended)
- memory per physical core: 5 GB or more gives a CPU-limited result; less
  completes, labeled memory-bound (the analyst jobs reserve up to 40 GB each)
- 80 GB free for run data (each load step writes the fleet databases)
- an idle server: under 3% of the CPU busy over 60 s (10 s with --quick);
  the busiest containers are listed when it is not
- the privileges the run needs: Docker, sudo + network namespaces for the
  sandboxed jobs, CPU pinning
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

MIN_CORES, REC_CORES = 32, 64
MIN_GB_PER_CORE = 5.0
MIN_FREE_GB = 80
IDLE_MAX_PCT = 3.0


def _read(p: str, default: str = "") -> str:
    try:
        return open(p).read().strip()
    except OSError:
        return default


def system_info() -> dict:
    cores, threads, sockets = set(), 0, set()
    for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology"):
        pkg = int(_read(p + "/physical_package_id", "0"))
        cores.add((pkg, int(_read(p + "/core_id", "0"))))
        sockets.add(pkg)
        threads += 1
    model = ""
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            model = re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
            break
    mem_kb = int(re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo", "MemTotal: 0")).group(1))
    vendor = _read("/sys/class/dmi/id/sys_vendor")
    product = _read("/sys/class/dmi/id/product_name")
    return {"vendor": vendor, "product": product,
            "system": " ".join(x for x in (vendor.replace("Dell Inc.", "Dell"), product) if x) or "This server",
            "processor": model, "sockets": len(sockets), "cores": len(cores), "threads": threads,
            "memory_gb": round(mem_kb / 1024 ** 2), "kernel": os.uname().release}


def _cpu_busy(seconds: float) -> float:
    def snap():
        f = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
        idle = f[3] + (f[4] if len(f) > 4 else 0)
        return sum(f), idle
    t1, i1 = snap()
    time.sleep(seconds)
    t2, i2 = snap()
    return 100.0 * (1 - (i2 - i1) / max(1, t2 - t1))


def _busy_containers() -> list[str]:
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.splitlines():
        name, pct = line.rsplit(" ", 1)
        try:
            v = float(pct.rstrip("%"))
        except ValueError:
            continue
        if v >= 5:
            rows.append((v, name))
    return [f"{n} ({v:.0f}% of one thread)" for v, n in sorted(rows, reverse=True)[:8]]


def check(quick: bool = False) -> dict:
    info = system_info()
    items = []

    def add(name, state, detail):
        items.append({"check": name, "state": state, "detail": detail})

    c = info["cores"]
    add("Physical cores", "fail" if c < MIN_CORES else "warn" if c < REC_CORES else "pass",
        f"{c} cores on {info['sockets']} socket{'s' if info['sockets'] != 1 else ''}, {info['threads']} threads"
        + ("" if c >= REC_CORES else f"; {REC_CORES} or more recommended" if c >= MIN_CORES else f"; at least {MIN_CORES} needed"))
    gbc = info["memory_gb"] / max(1, c)
    memory_bound = gbc < MIN_GB_PER_CORE
    add("Memory per core", "warn" if memory_bound else "pass",
        f"{info['memory_gb']} GB, {gbc:.1f} GB per core"
        + (f"; under {MIN_GB_PER_CORE:.0f} GB the result is labeled memory-bound" if memory_bound else ""))
    data = os.environ.get("BENCH_ROOT", "/opt/bench") + "/data"
    free = shutil.disk_usage(data if os.path.isdir(data) else "/").free / 1e9
    add("Free disk", "pass" if free >= MIN_FREE_GB else "fail", f"{free:.0f} GB free for run data; {MIN_FREE_GB} GB needed")
    busy = _cpu_busy(10 if quick else 60)
    hogs = [] if busy < IDLE_MAX_PCT else _busy_containers()
    add("Idle server", "pass" if busy < IDLE_MAX_PCT else "fail",
        f"{busy:.1f}% of the CPU busy" + (f"; stop the busiest containers first: {', '.join(hogs)}" if hogs else
                                          "; something else is using the CPU" if busy >= IDLE_MAX_PCT else ""))
    docker_ok = subprocess.call(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    add("Docker", "pass" if docker_ok else "fail", "reachable" if docker_ok else "the Docker socket is not reachable")
    netns = shutil.which("sudo") and os.system("sudo -n unshare -n true >/dev/null 2>&1") == 0
    add("Job isolation", "pass" if netns else "fail",
        "sandboxed jobs run in their own network namespace" if netns else "sudo unshare -n failed (is the container privileged?)")
    ports = _ports_in_use()
    add("Ports", "pass" if not ports else "fail",
        "the run's local ports are free" if not ports else "in use by other programs: " + ", ".join(ports[:8]))
    pin = os.system("taskset -c 0 true >/dev/null 2>&1") == 0
    add("CPU pinning", "pass" if pin else "fail", "taskset works" if pin else "taskset failed")
    failed = [i for i in items if i["state"] == "fail"]
    return {"ok": not failed, "memory_bound": memory_bound, "system": info, "checks": items,
            "summary": "Ready to measure" if not failed else "Not ready: " + "; ".join(f"{i['check']}: {i['detail']}" for i in failed)}


# Ports the run listens on (127.0.0.1): databases, instances, embedders and
# rerankers, model stand-ins, executors. The fleet scripts free their ports
# by ending the processes holding them, so another program on any of them
# must stop the run before it starts.
KIT_PORTS = [(5441, 5464), (8100, 8340), (8879, 8899), (8921, 8944), (9300, 16500)]


def _kit_container_ports() -> set[int]:
    """Host ports published by the kit's own containers (ratio-*), left from an earlier run."""
    try:
        out = subprocess.run(["docker", "ps", "--filter", "name=^ratio-", "--format", "{{.Ports}}"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return set()
    return {int(m) for m in re.findall(r":(\d+)->", out)}


def _ports_in_use() -> list[str]:
    try:
        out = subprocess.run(["ss", "-Htlnp"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return []
    kit_ports = _kit_container_ports()
    me = os.getuid()
    busy = []
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        addr, _, port_s = f[3].rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            continue
        if not any(a <= port <= b for a, b in KIT_PORTS) or port in kit_ports:
            continue
        # The run binds 127.0.0.1 only. A listener on one specific outside
        # address (an admin page on a VPN interface, say) does not block that;
        # a wildcard or loopback listener does.
        if addr not in ("*", "0.0.0.0", "[::]", "::", "%lo:0") and not addr.startswith(("127.", "[::1]", "::1", "[::ffff:127.")):
            continue
        pids = [int(p) for p in re.findall(r"pid=(\d+)", line)]
        ours = pids and all(os.stat(f"/proc/{p}").st_uid == me for p in pids if os.path.exists(f"/proc/{p}"))
        if not ours:
            name = re.search(r'\(\("([^"]+)"', line)
            busy.append(f"{port} ({name.group(1) if name else 'another program'})")
    return busy


def report(pf: dict) -> str:
    s = pf["system"]
    lines = [f"{s['system']} · {s['processor']} · {s['sockets']} sockets, {s['cores']} cores · {s['memory_gb']} GB", ""]
    for i in pf["checks"]:
        lines.append(f"  {i['state'].upper():5} {i['check']}: {i['detail']}")
    lines += ["", pf["summary"]]
    return "\n".join(lines)


if __name__ == "__main__":
    pf = check(quick="--quick" in sys.argv)
    print(json.dumps(pf, indent=1) if "--json" in sys.argv else report(pf))
    sys.exit(0 if pf["ok"] else 2)
