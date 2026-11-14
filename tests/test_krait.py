"""The Krait engine with pytrf's runs replaced (real runs: scripts/check_conda_tools.py)."""
import threading
import time

from cladia import config, jobs, library
from cladia.tool_plugins import krait

# what pytrf's command line writes, per search (tsv: seqid start end motif type repeats length [+ extra])
FAKE = {
    "findstr": "target\t1\t20\tAC\t2\t10\t20\n",
    "findatr": "target\t1\t26\tAC\t2\t12.0\t26\t1\t12\t6\t12\t24\t1\t0\t0\t92.3\n",
    "findgtr": "target\t1\t28\tGATTACA\t7\t4\t28\n",
}


def _fake_pytrf(job, image, args, workdir, mounts=None, entrypoint=None):
    assert args[0] == "env" and args[1].startswith("PATH=/opt/cladia-conda/envs/krait-1.5.0-")
    program = args[2:]
    assert program[0] == "pytrf"
    if program[1:] == ["--version"]:
        return "pytrf 1.5.0\n"
    out = program[program.index("-o") + 1]
    text = FAKE[program[1]]
    if program[1] == "findstr" and "GGG" in (workdir / program[-1].removeprefix("/data/")).read_text():  # the compound sample
        text = "target\t1\t14\tAC\t2\t7\t14\ntarget\t18\t32\tATG\t3\t5\t15\n"
    (workdir / out.removeprefix("/data/")).write_text(text, encoding="utf-8")
    return ""


def _finished(job):
    deadline = time.monotonic() + 20
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)
    for worker in threading.enumerate():
        if getattr(worker, "_target", None) is jobs._run_then_next:
            worker.join(20)
    assert job["status"] == "done", (job["status"], job.get("error"), job.get("log"))
    return job


def test_krait_methods_become_repeat_features(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "library" / ".cladia")
    monkeypatch.setattr(krait.api, "run_container", _fake_pytrf)
    library.ensure_folders()
    samples = [("perfect", "AC" * 10 + "GT", {}),
               ("compound", "AC" * 7 + "GGG" + "ATG" * 5 + "TC", {"max_distance": 3}),
               ("imperfect", "AC" * 6 + "AT" + "AC" * 6 + "GT", {}),
               ("generic", "GATTACA" * 4 + "CG", {})]
    for method, sequence, settings in samples:
        source = tmp_path / f"{method}.fa"
        source.write_text(f">{method}\n{sequence}\n", encoding="ascii")
        (item_id,), errors = library.import_paths([source])
        item = library.get_item(item_id)
        job = _finished(jobs.submit("krait", method, krait.run, resources=("compute", 1),
                                    operation="variants.microsatellites", items=[item], output="",
                                    method=method, apply=False, **settings))
        record = library.records(library.get_item(job["outputs"][0]["id"]))[0]
        found = [f for f in record.features if f.type == "repeat_region" and f.qualifiers.get("krait_method") == [method]]
        assert found, (method, job["log"])
        if method == "compound":
            assert "Components:" in " ".join(found[0].qualifiers["note"])
        if method == "imperfect":
            assert "identity=92.3" in " ".join(found[0].qualifiers["note"])
        if method == "generic":
            assert found[0].qualifiers["rpt_unit_seq"] == ["GATTACA"]
        assert "Du et al." in job["log"] or "10.1186/s12859-025-06168-3" in job["log"]


def test_krait_commands():
    params = dict(mono=12, di=7, tri=5, tetra=4, penta=4, hexa=4, atr_min_motif=1, atr_max_motif=6, seed_repeats=3,
                  seed_length=10, max_errors=3, min_identity=70, max_extend=2000, gtr_min_motif=10, gtr_max_motif=100,
                  gtr_min_repeats=3, gtr_min_length=10)
    assert krait._command("perfect", params, "/data/in.fa", "/data/out.tsv") == [
        "findstr", "-r", 12, 7, 5, 4, 4, 4, "-f", "tsv", "-o", "/data/out.tsv", "/data/in.fa"]
    assert krait._command("generic", params, "a", "b")[:3] == ["findgtr", "-m", 10]
