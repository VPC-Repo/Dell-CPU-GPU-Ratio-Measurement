"""Core pools, instances and the starting arrival rate for this server.

    python -m runner.plan [topology.txt]        # prints the plan as JSON

The reference server is the PowerEdge R770 of record: two sockets of 86
physical cores, each giving the reranker 6 cores (first hardware thread
only, two processes of three threads), the query embedder 1 core and the
document embedder 2 cores, with the remaining 77 cores for the agents and
their jobs. Eight instances (about 19 application cores each, 28
executors each) sustained 1.84 workflows/s with 150 of 172 cores busy.

Another server keeps the same proportions per socket: the services' load
grows with the workflows a socket can run, which grows with its cores.
Services take the highest core ids of each socket, as on the reference.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import sys

REF_SOCKET_CORES = 86
REF_RERANK, REF_EMBED, REF_INGEST = 6, 1, 2       # per socket
REF_APP_CORES = 154                               # whole server
REF_INSTANCES = 8
REF_RATE = 1.84                                   # workflows/s at capacity, whole server
EXECUTORS = 28                                    # per instance
RERANK_THREADS = 3                                # per reranker process


def read_topology(path: str | None = None) -> dict[int, dict[int, list[int]]]:
    """{package: {core_id: [logical cpus]}} from a saved topology.txt
    (`cpu:siblings pkg=P core=C` per line) or from this machine's /sys."""
    by: dict[int, dict[int, list[int]]] = {}
    if path:
        for line in open(path):
            m = re.match(r"(\d+):\S+ pkg=(\d+) core=(\d+)", line)
            if m:
                cpu, pkg, cid = (int(x) for x in m.groups())
                by.setdefault(pkg, {}).setdefault(cid, []).append(cpu)
    else:
        for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology"):
            cpu = int(p.split("/")[5][3:])
            pkg = int(open(p + "/physical_package_id").read())
            cid = int(open(p + "/core_id").read())
            by.setdefault(pkg, {}).setdefault(cid, []).append(cpu)
    for pkg in by:
        for cid in by[pkg]:
            by[pkg][cid].sort()
    return by


def services_per_socket(cores: int) -> tuple[int, int, int]:
    """(reranker, query embedder, document embedder) cores for a socket."""
    scale = cores / REF_SOCKET_CORES
    rer = max(RERANK_THREADS, RERANK_THREADS * round(REF_RERANK * scale / RERANK_THREADS))
    return rer, max(1, round(REF_EMBED * scale)), max(1, round(REF_INGEST * scale))


def plan(topology: dict[int, dict[int, list[int]]]) -> dict:
    rer, emb, ing, rest = [], [], [], []
    per_socket = {}
    for pkg in sorted(topology):
        cores = [cpus for _, cpus in sorted(topology[pkg].items())]
        n_r, n_e, n_i = services_per_socket(len(cores))
        top = cores[::-1]                                   # services take the highest core ids
        rer += [c[0] for c in top[:n_r]]
        emb += [x for c in top[n_r:n_r + n_e] for x in c]
        ing += [x for c in top[n_r + n_e:n_r + n_e + n_i] for x in c]
        rest += [x for c in top[n_r + n_e + n_i:] for x in c]
        per_socket[pkg] = {"cores": len(cores), "reranker": n_r, "query_embedder": n_e,
                           "document_embedder": n_i, "application": len(cores) - n_r - n_e - n_i}
    total = sum(len(v) for v in topology.values())
    threads = sum(len(c) for v in topology.values() for c in v.values())
    app = sum(s["application"] for s in per_socket.values())
    instances = max(1, round(app * REF_INSTANCES / REF_APP_CORES))
    box_rate = REF_RATE * app / REF_APP_CORES
    j = lambda xs: ",".join(map(str, sorted(xs)))
    return {
        "sockets": len(topology), "cores": total, "threads": threads, "smt": threads > total,
        "per_socket": per_socket, "application_cores": app,
        "instances": instances, "executors_per_instance": EXECUTORS,
        "reranker_processes": max(1, len(rer) // RERANK_THREADS), "reranker_threads": RERANK_THREADS,
        "cpusets": {"reranker": j(rer), "query_embedder": j(emb), "document_embedder": j(ing), "application": j(rest)},
        # The first step's arrival rate, scaled from the reference by application cores.
        "predicted_rate": round(box_rate, 3),
        "predicted_rate_per_instance": round(box_rate / instances, 4),
    }


def env(p: dict) -> str:
    """Shell exports for the fleet scripts."""
    c = p["cpusets"]
    return "\n".join([
        f"export K={p['instances']} FLEET_K={p['instances']} W={p['executors_per_instance']} FLEET_W={p['executors_per_instance']}",
        f"export RERANK_WORKERS={p['reranker_processes']} RERANK_THREADS={p['reranker_threads']}",
        f"export RERANK_CPUS={c['reranker']} EMBED_CPUS={c['query_embedder']} INGEST_EMBED_CPUS={c['document_embedder']} REST_CPUS={c['application']}",
        f"export RERANK_PHYS_CORES={len(c['reranker'].split(','))}",
    ])


if __name__ == "__main__":
    p = plan(read_topology(sys.argv[1] if len(sys.argv) > 1 else None))
    print(env(p) if os.getenv("PLAN_FORMAT") == "env" else json.dumps(p, indent=1))
