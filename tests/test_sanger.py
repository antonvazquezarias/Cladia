import cladia.tool_plugins.sanger as owner_sanger
"""Sanger reads: .ab1 import, quality trimming, the trace beside the sequence.

read_qual.ab1 and read_noqual.ab1 are synthetic traces (tests/make_test_data.py):
an "ABI 3100" read with qualities, and a "310" one without.
"""

import base64
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, library, sanger
from cladia.genbank import read_records

DATA = Path(__file__).parent / "data"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    return TestClient(app)


def test_read_and_trim():
    trace = sanger.read_trace(DATA / "read_qual.ab1")
    assert len(trace["bases"]) == len(trace["quality"]) == len(trace["peaks"]) == 795
    assert sorted(trace["channels"]) == ["A", "C", "G", "T"] and len(trace["channels"]["A"]) == 9579
    assert trace["sample"] == "SYN_S2_cox1F" and trace["machine"] == "3100"
    start, end = sanger.trim(trace)
    assert (start, end) == (18, 697)  # (the made-up qualities are low for 18 bases and the last 98)
    assert sanger.trim(trace, 0) == (0, 795)  # no limit: all of it
    # a read without qualities is kept whole
    old = sanger.read_trace(DATA / "read_noqual.ab1")
    assert old["quality"] == [] and sanger.trim(old) == (0, 868)


def test_reads_in_the_library(client, tmp_path):
    files = tmp_path / "reads"
    files.mkdir()
    shutil.copy(DATA / "read_qual.ab1", files / "R6574_F.ab1")
    shutil.copy(DATA / "read_noqual.ab1", files / "R6574_R.whatever")  # known by its content
    r = client.post("/api/library/add", json={"paths": [str(files / "R6574_F.ab1"), str(files / "R6574_R.whatever")]}).json()
    assert r["errors"] == []
    found = {i["label"]: i for i in client.get("/api/library").json()}
    read = found["R6574_F"]
    assert read["kind"] == "sequence" and read["trace"] and read["length"] == 679
    (record,) = read_records(read["path"], "genbank")
    assert str(record.seq) == sanger.read_trace(DATA / "read_qual.ab1")["bases"][18:697]
    assert "SYN_S2_cox1F" in record.description and "bases 19-697 of 795" in record.description
    trace = client.get(f"/api/trace/{read['id']}").json()
    assert (trace["start"], trace["end"]) == (18, 697) and len(trace["peaks"]) == 795
    assert found["R6574_R"]["length"] == 868  # no qualities: whole
    assert client.get(f"/api/trace/{found['R6574_R']['id']}").status_code == 200

    # trimmed again: the whole read, then the default again
    assert client.post(f"/api/trace/{read['id']}/trim", json={"limit": 0}).json() == {"start": 0, "end": 795, "length": 795}
    assert len(read_records(read["path"], "genbank")[0]) == 795
    client.post(f"/api/trace/{read['id']}/trim", json={})
    assert len(read_records(read["path"], "genbank")[0]) == 679

    # not into a list; duplicated and deleted with its trace
    assert client.post("/api/library/group", json={"ids": [read["id"], found["R6574_R"]["id"]], "name": "x"}).status_code == 400
    copy = client.post("/api/library/duplicate", json={"ids": [read["id"]]}).json()["ids"][0]
    copy_trace = library.get_item(copy)["trace"]
    assert copy_trace != read["trace"] and (library.library_dir() / copy_trace).exists()
    client.post("/api/library/remove", json={"ids": [read["id"]]})
    assert not (library.library_dir() / read["trace"]).exists() and (library.library_dir() / copy_trace).exists()

    # browser mode: the file's bytes uploaded
    data = base64.b64encode((DATA / "read_qual.ab1").read_bytes()).decode()
    up = client.post("/api/library/upload", json={"filename": "uploaded.ab1", "data": data}).json()
    assert library.get_item(up["ids"][0])["trace"]


def test_assemble_forward_and_reverse(client, tmp_path):
    """A forward read and a reverse one made from it (its trace turned round, the
    part from base 300, one base changed at low quality): one consensus, the
    reverse read reverse complemented, the disagreement called by quality."""
    import json
    from cladia import jobs
    from cladia.genbank import write_record
    shutil.copy(DATA / "read_qual.ab1", tmp_path / "S1_F.ab1")
    (fid,), _ = library.import_paths([tmp_path / "S1_F.ab1"])
    (rid,), _ = library.import_paths([tmp_path / "S1_F.ab1"])
    library.rename(library.get_item(rid), "S1_R")
    f = library.trace_data(library.get_item(fid))
    n, scans = len(f["bases"]), max(len(c) for c in f["channels"].values()) - 1
    swap = {"A": "T", "C": "G", "G": "C", "T": "A"}
    bases = sanger.revcomp(f["bases"])
    at = n - 1 - 500  # forward base 500, in the reverse read
    bases = bases[:at] + swap[bases[at]] + bases[at + 1:]
    quality = f["quality"][::-1]
    quality[at] = 5
    r = {**f, "bases": bases, "quality": quality, "peaks": [scans - p for p in reversed(f["peaks"])],
         "channels": {swap[b]: c[::-1] for b, c in f["channels"].items()}, "start": n - f["end"], "end": n - 300}
    item = library.get_item(rid)
    (library.library_dir() / item["trace"]).write_text(json.dumps(r))
    write_record(sanger.read_record(r, "S1_R", r["start"], r["end"]), Path(item["path"]))

    job = {"id": "a1", "log": "", "outputs": []}
    (tmp_path / "w").mkdir()
    owner_sanger.run_one(job, tmp_path / "w", [library.get_item(fid), library.get_item(rid)], "S1")
    aln, consensus = (library.get_item(o["id"]) for o in job["outputs"])
    assert aln["kind"] == "alignment" and aln["label"] == "S1 reads" and consensus["label"] == "S1"
    assert [r.id for r in read_records(aln["path"], "fasta")] == ["S1_F", "_R_S1_R"]
    (record,) = read_records(consensus["path"], "genbank")
    assert str(record.seq) == f["bases"][f["start"]:f["end"]]  # the forward read's call wins (quality 5 lost)
    (mark,) = [x for x in record.features if x.type == "misc_feature"]
    assert int(mark.location.start) == 500 - f["start"] and "q5 (S1_R)" in mark.qualifiers["note"][0]
    # the alignment view: both rows are reads, with their traces
    rows = client.get(f"/api/alignment/{aln['id']}/features").json()["rows"]
    assert rows["S1_F"]["trace"] and rows["_R_S1_R"]["trace"] and rows["_R_S1_R"]["offset"] == 0


def test_samples_by_name():
    """The sample is what comes before the primer word; what follows it (a plate well,
    a run number) is left out; reads without a partner are left alone."""
    assert sanger.sample_of("AG123_rbcLF") == ("AG123", "rbcLF")
    assert sanger.sample_of("AG124-R_B01") == ("AG124", "R")
    assert sanger.sample_of("G_ex_ABC12_COI-F1_A03_88") == ("G_ex_ABC12_COI", "F1")
    assert sanger.sample_of("S1.rev") == ("S1", "rev")
    assert sanger.sample_of("nodirection")[1] is None
    paired, alone = sanger.samples_by_name(["AG123_rbcLF", "AG123_rbcLR", "AG123_intF", "AG124-F_A01",
                                            "AG124-R_B01", "lonely_F", "nodirection"])
    assert paired == {"AG123": ["AG123_rbcLF", "AG123_rbcLR", "AG123_intF"], "AG124": ["AG124-F_A01", "AG124-R_B01"]}
    assert alone == ["lonely_F", "nodirection"]


def test_assemble_by_sample(client, tmp_path, monkeypatch):
    """Two samples' reads (copies of one trace) and a read alone: each sample's
    alignment and one list of consensus sequences, in a new folder."""
    from cladia import jobs
    shutil.copy(DATA / "read_qual.ab1", tmp_path / "r.ab1")
    ids = []
    for name in ("A1_F", "A1_R", "B2_F", "B2_R", "C3_F"):
        (new,), _ = library.import_paths([tmp_path / "r.ab1"])
        library.rename(library.get_item(new), name)
        ids.append(new)
    groups = client.post("/api/sanger/samples", json={"ids": ids}).json()
    assert [g["sample"] for g in groups["samples"]] == ["A1", "B2"] and groups["alone"] == ["C3_F"]
    by_name = {library.get_item(i)["name"]: library.get_item(i) for i in ids}
    job = {"id": "b1", "log": "", "outputs": [], "folder": None}
    (tmp_path / "w").mkdir()
    owner_sanger.run_batch(job, tmp_path / "w", [("A1", [by_name["A1_F"], by_name["A1_R"]]),
                                                  ("B2", [by_name["B2_F"], by_name["B2_R"]])], "Run 1")
    labels = [o["label"] for o in job["outputs"]]
    assert labels == ["A1 reads", "B2 reads", "Run 1 consensus"]
    consensus = library.get_item(job["outputs"][-1]["id"])
    assert consensus["kind"] == "list" and [r.id for r in library.records(consensus)] == ["A1", "B2"]
    assert job["folder"] == "Run 1" and "2 of 2 samples assembled" in job["log"]
    # through the endpoint: a job of its own, by sample
    submitted = []
    def submit(kind, title, func, **params):
        submitted.append((kind, func, params))
        return {"title": title}
    monkeypatch.setattr(jobs, "submit", submit)
    r = client.post("/api/tool-registry/run", json={"operation": "assemble.batch", "engine": "sanger",
        "inputs": {"reads": ids}, "output": "Run 2"})
    assert r.status_code == 200 and r.json()["title"] == "Assemble 2 samples"
    assert submitted[0][0] == "assemble" and callable(submitted[0][1])
    assert [name for name, _ in submitted[0][2]["samples"]] == ["A1", "B2"]
