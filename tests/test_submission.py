import cladia.execution as owner_execution
import cladia.internal_jobs as owner_internal_jobs
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from cladia import submission


def record():
    r = SeqRecord(Seq("ATGAAATAA" * 10), id="x", name="x_1")
    r.annotations.update(molecule_type="DNA", topology="circular", organism="Gracilaria sp.")
    r.features = [
        SeqFeature(FeatureLocation(0, 90, strand=1), type="source", qualifiers={"organelle": ["mitochondrion"], "strain": ["s1"]}),
        SeqFeature(FeatureLocation(9, 27, strand=1), type="gene", qualifiers={"label": ["cox1"]}),
        SeqFeature(CompoundLocation([FeatureLocation(30, 39, strand=-1), FeatureLocation(9, 18, strand=-1)]), type="CDS",
                   qualifiers={"product": ["cox"], "translation": ["MK"], "note": ["copied from Y, 90 % identical", "ok"]}),
    ]
    return r


def test_fasta_header():
    head = submission.fasta(record()).splitlines()[0]
    assert head == ">x_1 [organism=Gracilaria sp.] [moltype=genomic] [location=mitochondrion] [topology=circular] [gcode=4] [strain=s1]"


def test_table():
    t = submission.table(record(), "GS").splitlines()
    assert t[0] == ">Feature x_1"
    assert "10\t27\tgene" in t and "\t\t\tgene\tcox1" in t and "\t\t\tlocus_tag\tGS_001" in t
    assert "39\t31\tCDS" in t and "18\t10" in t           # minus strand: stop < start, parts in biological order
    assert not any("translation" in x or "copied from" in x for x in t)
    assert "\t\t\tnote\tok" in t


def test_endpoints_on_a_real_genome(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from cladia import config, genbank, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    text = (Path(__file__).parent / "data" / "synthetic_mt.gb").read_text()
    item = client.post("/api/library/upload", json={"filename": "mt.gb", "text": text}).json()
    item_id = (item.get("ids") or [item.get("id")])[0] if isinstance(item, dict) else item[0]
    data = client.get(f"/api/sequences/{item_id}").json()
    found = client.post("/api/checks", json={"sequences": [data]}).json()[0]
    assert "problems" in found and found["errors"] >= 0
    out = client.post("/api/export/submission", json={"ids": [item_id], "locus_tag_prefix": "AB12"}).json()
    assert out["fsa"].startswith(">") and out["tbl"].startswith(">Feature") and "AB12_001" in out["tbl"]
    assert client.post("/api/export/submission", json={"ids": [item_id], "locus_tag_prefix": "1x"}).status_code == 400
    path = tmp_path / "sub.tbl"
    client.post("/api/export/submission", json={"ids": [item_id], "path": str(path)})
    assert path.exists() and path.with_suffix(".fsa").exists()


def test_validate_job_logs_the_report(tmp_path, monkeypatch):
    from cladia import jobs
    monkeypatch.setattr(owner_execution, "ensure_tools_image", lambda job: None)
    monkeypatch.setattr(owner_execution, "ensure_image", lambda job, image: None)

    def fake(image, args, workdir, name=None, **kw):
        assert args[0] == "table2asn" and (workdir / "sub.fsa").exists() and (workdir / "sub.tbl").exists()
        (workdir / "sub.val").write_text("ERROR: SEQ_FEAT.NoStop x\n", encoding="utf-8")
        return 0, "", "                Warning:\nProblem: x\n", ["table2asn"]
    monkeypatch.setattr(owner_execution, "run_container", fake)
    job = {"id": "j", "log": ""}
    owner_internal_jobs.validate_job(job, tmp_path, [record()], "GS")
    assert "NoStop" in job["log"] and "2 error/warning lines" in job["log"]
