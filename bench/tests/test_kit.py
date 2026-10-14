"""The kit's own logic: core planning from any topology, the ratio label."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from runner import plan as P
from runner.results import ratio_label

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "topology-2s-86c.txt")


def test_plan_reproduces_the_reference_allocation():
    p = P.plan(P.read_topology(FIX))
    assert (p["sockets"], p["cores"], p["threads"]) == (2, 172, 344)
    assert p["application_cores"] == 154 and p["instances"] == 8 and p["reranker_processes"] == 4
    assert p["predicted_rate"] == 1.84 and p["predicted_rate_per_instance"] == 0.23
    sizes = {k: len(v.split(",")) for k, v in p["cpusets"].items()}
    # reranker one thread per core (12), embedders and application both threads
    assert sizes == {"reranker": 12, "query_embedder": 4, "document_embedder": 8, "application": 308}


def test_plan_scales_to_other_servers():
    for cores in (32, 64, 96, 128):
        topo = {0: {i: [i, i + cores] for i in range(cores)}}
        p = P.plan(topo)
        s = p["per_socket"][0]
        assert s["reranker"] % P.RERANK_THREADS == 0 and s["reranker"] >= P.RERANK_THREADS
        assert s["application"] == cores - s["reranker"] - s["query_embedder"] - s["document_embedder"]
        assert p["instances"] >= 1 and 0 < p["predicted_rate"] < P.REF_RATE
        used = [c for v in p["cpusets"].values() for c in v.split(",")]
        assert len(used) == len(set(used))          # no cpu in two pools


def test_ratio_label():
    assert ratio_label(1.54) == "2:3"
    assert ratio_label(1.0) == "1:1"
    assert ratio_label(0.5) == "2:1"
    assert ratio_label(0.04) == "25:1"
    assert ratio_label(0) == "-"


def test_install_without_optional_env_file(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copy(root / "install.sh", tmp_path)
    tools = tmp_path / "bin"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text('#!/bin/bash\nif [ "$*" = "compose version --short" ]; then echo 2.24.0; fi\n')
    docker.chmod(0o755)
    sleep = tools / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n")
    sleep.chmod(0o755)
    result = subprocess.run(["bash", "install.sh"], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    assert "Installed. Next:" in result.stdout
    assert "ssh -L 8766:127.0.0.1:8766" in result.stdout


@pytest.mark.parametrize("ledgers,reports", [(0, 0), (1, 1), (2, 1)])
def test_judge_rejects_missing_instance_records(tmp_path, ledgers, reports):
    from runner.measure import judge
    for i in range(ledgers):
        (tmp_path / f"rate-0.23-i{i + 1}-evidence-test.jsonl.gz").touch()
    for i in range(reports):
        (tmp_path / f"rate-0.23-i{i + 1}-capacity-test.json").touch()
    result = judge(str(tmp_path), 0.23, 2)
    assert result["passed"] is False
    assert "incomplete records" in result["error"]


def test_judge_requires_both_capacity_conditions(tmp_path, monkeypatch):
    from backend.capacity import judge as J
    from runner.measure import judge
    (tmp_path / "rate-0.23-i1-evidence-test.jsonl.gz").touch()
    (tmp_path / "rate-0.23-i1-capacity-test.json").touch()
    for keeps_up, generator_ok in [(True, True), (True, False), (False, True)]:
        monkeypatch.setattr(J, "plateau", lambda files: {"keeps_up": keeps_up, "generator_ok": generator_ok})
        assert judge(str(tmp_path), 0.23, 1)["passed"] is (keeps_up and generator_ok)


def test_archiving_removes_previous_step_records(tmp_path, monkeypatch):
    from runner import measure
    monkeypatch.setattr(measure, "DATA", str(tmp_path))
    fleet = tmp_path / "capacity" / "fleet1"
    fleet.mkdir(parents=True)
    old = fleet / "evidence-old.jsonl.gz"
    old.write_bytes(b"previous step")
    run = tmp_path / "run"
    measure.archive_fleet_files(str(run))
    assert not old.exists()
    assert (run / "previous-fleet-files" / "fleet1" / old.name).read_bytes() == b"previous step"
