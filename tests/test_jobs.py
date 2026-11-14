import cladia.tool_plugins.minimap2 as owner_minimap2
import cladia.tool_plugins.fasttree as owner_fasttree
import cladia.execution as owner_execution
import cladia.job_progress as owner_job_progress
import cladia.tool_plugins.fasttree as owner_fasttree
import cladia.tool_plugins.iqtree as owner_iqtree
import cladia.tool_plugins.mafft as owner_mafft
import cladia.tool_plugins.mauve as owner_mauve
import cladia.tool_plugins.ncbi_blast as owner_ncbi_blast
import cladia.tool_plugins.orfs as owner_orfs
import cladia.tool_plugins.primer3 as owner_primer3
import cladia.tool_plugins.primer_test as owner_primer_test
"""Genetic codes and the jobs (containers replaced by stand-ins).

Run with:  python -m pytest tests
"""

import json
import os
import re
import shlex
import shutil
import threading
import time
from pathlib import Path

import pytest
from Bio.Seq import Seq
from Bio.SeqFeature import SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
from fastapi.testclient import TestClient

from cladia import config, jobs, library
from cladia.genbank import genetic_code, read_records, write_record, read_record

DATA = Path(__file__).parent / "data"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Empty data folder; containers record their arguments instead of running."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    files = tmp_path / "my files"
    files.mkdir()
    shutil.copy(DATA / "synthetic_mt.gb", files / "synthetic_mt.gb")
    (files / "set.fasta").write_text(">strainA\nACGTACGT\n>strainB\nACGTTCGT\n")
    (files / "aln.fasta").write_text(">strainA\nACGT-ACGT\n>old\nACGTTACGT\n")
    calls = []
    monkeypatch.setattr(owner_execution, "ensure_tools_image", lambda job: None)
    monkeypatch.setattr(owner_execution, "tool", lambda job, image, args, workdir: calls.append((_program(args), workdir)) or ">x\nACGT\n")
    return files, calls


def _program(args):
    """The program's own command, out of the bash -c "... 2>/data/run.log" that
    owner_execution.tool_watched wraps it in."""
    if args[:2] == ["bash", "-c"] and args[2].endswith(" 2>/data/run.log"):
        return shlex.split(args[2][:-len(" 2>/data/run.log")])
    return args


def _items(files, *names):
    """Imports files; returns their sequence items (a list's sequences) or alignments."""
    ids, errors = library.import_paths([files / n for n in names])
    assert errors == []
    return [child for i in ids for child in library.children(library.get_item(i))]


def test_job_provenance_survives_trash_restore(setup):
    files, _ = setup
    source = _items(files, "synthetic_mt.gb")[0]
    job = {"id": "provenance-test", "kind": "orfs", "title": "Find ORFs",
           "log": "", "outputs": [], "created": time.time(), "folder": None}

    def run(job, workdir, item):
        result = library.add_sequence(library.records(item)[0], "result")
        job["outputs"].append({"id": result, "label": "result", "kind": "sequence"})

    jobs._run(job, run, {"item": source})
    result_id = job["outputs"][0]["id"]
    provenance = library.get_item(result_id)["provenance"]
    assert provenance["job_id"] == job["id"] and provenance["status"] == "done"
    assert provenance["inputs"] == [{"id": source["id"], "label": source["label"]}]
    library.remove([result_id], trash=True)
    restored = library.restore([library.trash_items()[0]["id"]])[0]
    assert library.get_item(restored)["provenance"] == provenance


# ── Genetic code ─────────────────────────────────────────────────────────

def _record(organelle=None, transl_table=None):
    record = SeqRecord(Seq("ATG" * 10))
    if organelle:
        record.features.append(SeqFeature(SimpleLocation(0, 30), type="source", qualifiers={"organelle": [organelle]}))
    if transl_table:
        record.features.append(SeqFeature(SimpleLocation(0, 30), type="CDS", qualifiers={"transl_table": [transl_table]}))
    return record


def test_genetic_code_from_file():
    assert genetic_code(_record()) == 1
    assert genetic_code(_record("mitochondrion")) == 4
    assert genetic_code(_record("plastid:chloroplast")) == 11
    assert genetic_code(_record("mitochondrion", transl_table="1")) == 1  # the CDS says so
    assert genetic_code(read_record(DATA / "synthetic_mt.gb")) in (1, 4)


def test_genetic_codes_api():
    from cladia.server import app
    codes = {c["id"]: c for c in TestClient(app).get("/api/genetic-codes").json()}
    assert codes[1]["codons"]["TGA"] == "*"
    assert codes[4]["codons"]["TGA"] == "W"
    assert len(codes[4]["codons"]) == 64


# ── ORF finder ───────────────────────────────────────────────────────────

def test_orfs_job(setup, tmp_path):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    job = {"log": "", "outputs": []}
    owner_orfs.run_annotation(job, tmp_path, [item], code=0, min_codons=10, both_strands=True,
                  any_start=False, skip_cds=False, replace=True)
    assert job["outputs"] and job["outputs"][0]["label"].endswith("_orfs")
    out = library.get_item(job["outputs"][0]["id"])
    record = read_records(Path(out["path"]), "genbank")[0]
    types = [f.type for f in record.features]
    assert "ORF" in types and "gene" not in types  # replace: only the ORFs (and the source)
    assert any("open reading frame" in " ".join(f.qualifiers.get("note", [])) for f in record.features)


def test_orfs_endpoint(setup, monkeypatch):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    calls = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: calls.append((kind, title, func, kw)) or {"id": "job"})
    from cladia.server import app
    r = TestClient(app).post("/api/tool-registry/run", json={"operation": "annotate.orfs", "engine": "orfs",
        "inputs": {"sequences": [item["id"]]}, "options": {"min_codons": 5, "both_strands": False}})
    assert r.status_code == 200
    kind, title, func, kw = calls[0]
    assert kind == "orfs" and callable(func) and title.startswith("Find ORFs")
    assert kw["items"][0]["id"] == item["id"] and kw["min_codons"] == 5 and kw["both_strands"] is False
    assert kw["into"] is None and kw["skip_cds"] is True
    assert kw["apply"] is True and kw["items"][0]["annotation_hash"]


def test_orfs_update_list_and_restore_previous_state(setup, tmp_path):
    files, _ = setup
    (files / "set.fasta").write_text(">strainA\nATGAAATAA\n>strainB\nATGCCCTAA\n")
    (list_id,), _ = library.import_paths([files / "set.fasta"])
    members = library.children(library.get_item(list_id))
    before = Path(members[0]["path"]).read_bytes()
    library.annotation_baselines(members)
    job = {"log": "", "outputs": []}
    owner_orfs.run_annotation(job, tmp_path, members, min_codons=1, skip_cds=False, apply=True, busy=lambda ids: False)
    assert [out["id"] for out in job["outputs"]] == [list_id]
    assert job["outputs"][0]["updated"] is True
    timeline = library.saved_versions(library.get_item(list_id))
    assert len(timeline["versions"]) == 2
    library.restore_version(library.get_item(list_id), 0)
    assert Path(members[0]["path"]).read_bytes() == before


@pytest.mark.parametrize("busy", [False, True])
def test_annotation_changed_or_busy_preserves_source_and_creates_copy(setup, tmp_path, busy):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    library.annotation_baselines([item])
    if not busy:
        library.edit_metadata(item, {"description": "changed while running"})
    before = Path(item["path"]).read_bytes()
    job = {"log": "", "outputs": []}
    owner_orfs.run_annotation(job, tmp_path, [item], min_codons=10, apply=True, busy=lambda ids: busy)
    assert Path(item["path"]).read_bytes() == before
    assert job["outputs"][0]["id"] != item["id"] and job["warning"]


def test_annotation_refuses_unexpected_base_changes(setup, tmp_path):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    library.annotation_baselines([item])
    original = library.records(item)[0]
    before = Path(item["path"]).read_bytes()
    original.seq = Seq("A" * len(original.seq))
    candidate = tmp_path / "candidate.gb"
    write_record(original, candidate)
    assert library.apply_annotation_group([(item, candidate)], lambda ids: False) is False
    assert Path(item["path"]).read_bytes() == before


def test_orfs_on_a_list(setup):
    files, _ = setup
    (lst,) = library.import_paths([files / "set.fasta"])[0]
    single = _items(files, "synthetic_mt.gb")[0]
    from cladia.server import app
    started = TestClient(app).post("/api/tool-registry/run", json={"operation": "annotate.orfs", "engine": "orfs",
        "inputs": {"sequences": [single["id"], lst]}, "options": {"min_codons": 5, "replace": True},
        "save_copy": True}).json()
    assert [j["title"] for j in started] == ["Find ORFs: set (2 sequences)", "Find ORFs: Mt"]
    while any(jobs.JOBS[j["id"]]["status"] in ("queued", "running") for j in started):
        time.sleep(0.05)
    found = {i["label"]: i for i in library.items()}
    assert found["set_orfs"]["kind"] == "list" and found["set_orfs"]["count"] == 2
    assert found["Mt_orfs"]["kind"] == "sequence"


# ── Primers ──────────────────────────────────────────────────────────────

PRIMER3_OUT = """PRIMER_LEFT_0_SEQUENCE=AAAACCCC
PRIMER_RIGHT_0_SEQUENCE=TTTTGGGG
PRIMER_LEFT_0_POSITION=10,8
PRIMER_RIGHT_0_POSITION=107,8
PRIMER_LEFT_0_TM=60.1
PRIMER_RIGHT_0_TM=61.2
PRIMER_LEFT_0_GC_PERCENT=50
PRIMER_RIGHT_0_GC_PERCENT=50
PRIMER_PAIR_0_PRODUCT_SIZE=98
PRIMER_PAIR_0_PENALTY=1.25
"""


def test_record_metadata(setup):
    files, _ = setup
    sequence = _items(files, "synthetic_mt.gb")[0]
    saved = library.edit_metadata(sequence, {"description": "A test genome", "organism": "Kappaphycus alvarezii",
                                             "accession": "TEST_9.1", "strain": "S1", "isolate": ""})
    assert {k: saved[k] for k in ("description", "organism", "accession", "strain", "isolate")} == {
        "description": "A test genome", "organism": "Kappaphycus alvarezii",
        "accession": "TEST_9.1", "strain": "S1", "isolate": ""}
    refreshed = library.get_item(sequence["id"])
    assert refreshed["description"] == "A test genome" and refreshed["organism"] == "Kappaphycus alvarezii"
    record = library.records(refreshed)[0]
    source = next(f for f in record.features if f.type == "source")
    assert source.qualifiers["strain"] == ["S1"] and source.qualifiers["organism"] == ["Kappaphycus alvarezii"]
    library.edit_metadata(refreshed, {"country": "Spain", "collection_date": "2024", "host": "alga"})
    metadata = library.record_metadata(library.get_item(sequence["id"]))
    assert (metadata["country"], metadata["collection_date"], metadata["host"]) == ("Spain", "2024", "alga")
    assert metadata["organism"] == "Kappaphycus alvarezii" and metadata["references"] == saved["references"]
    (list_id,), _ = library.import_paths([files / "set.fasta"])
    list_item = library.get_item(list_id)
    member = library.children(list_item)[0]
    library.edit_metadata(member, {"description": "Member", "organism": "Species two",
                                   "accession": member["label"], "strain": "", "isolate": ""})
    assert library.get_item(member["id"])["description"] == "Member"
    assert len(library.records(library.get_item(list_item["id"]))) == 2  # the other record is intact
    with pytest.raises(ValueError):
        alignment = _items(files, "aln.fasta")[0]
        library.edit_metadata(alignment, {"description": "nope"})


def test_save_sequence_and_metadata_together(setup):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    from cladia.server import app
    client = TestClient(app)
    data = client.get(f"/api/sequences/{item['id']}").json()
    data["sequence"] += "ACGT"
    data["metadata"] = {"organism": "Test species", "country": "Spain"}
    assert client.put(f"/api/sequences/{item['id']}", json=data).status_code == 200
    refreshed = library.get_item(item["id"])
    assert str(library.records(refreshed)[0].seq).endswith("ACGT")
    metadata = library.record_metadata(refreshed)
    assert metadata["organism"] == "Test species" and metadata["country"] == "Spain"


def test_primer_design_job(setup, tmp_path, monkeypatch):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    monkeypatch.setattr(owner_execution, "tool", lambda job, image, args, workdir: PRIMER3_OUT)
    job = {"log": "", "outputs": []}
    owner_primer3.run_design(job, tmp_path, item, product_min=50, product_max=200)
    assert [output["kind"] for output in job["outputs"]] == ["primers"]
    result = library.primers_data(library.get_item(job["outputs"][0]["id"]))
    assert result["mode"] == "design" and result["date"]
    assert result["parameters"]["product_min"] == 50 and result["parameters"]["num_return"] == 5
    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["left"]["start"] == 11
    assert result["pairs"][0]["left"]["tm"] == 60.1
    assert result["pairs"][0]["product_size"] == 98
    assert result["pairs"][0]["penalty"] == 1.25


def test_primer_test_job(setup, tmp_path):
    files, _ = setup
    item = _items(files, "set.fasta")[0]
    job = {"log": "", "outputs": []}
    owner_primer_test.run_test(job, tmp_path, [item], "ACGTACGT", "ACGTACGT", product_min=8,
                         product_max=20, both_orientations=False)
    assert [output["kind"] for output in job["outputs"]] == ["primers"]
    result = library.primers_data(library.get_item(job["outputs"][0]["id"]))
    assert result["mode"] == "test" and result["hits"]
    assert result["parameters"]["both_orientations"] is False
    assert result["sequences"] == 1


def test_primer_endpoint(setup, monkeypatch):
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    calls = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: calls.append((kind, title, func, kw)) or {"id": "job"})
    from cladia.server import app
    client = TestClient(app)
    r = client.post("/api/tool-registry/run", json={"operation": "primers.design", "engine": "primer3",
        "inputs": {"template": [item["id"]]}, "options": {"target_start": 1, "target_end": 100}})
    assert r.status_code == 200
    kind, title, func, kw = calls[0]
    assert kind == "primers" and callable(func) and title.startswith("Design primers")
    assert kw["target_start"] == 0 and kw["target_end"] == 99
    assert kw["folder"] is None  # the results go where the template is
    calls.clear()
    r = client.post("/api/tool-registry/run", json={"operation": "primers.test", "engine": "primer_test",
        "inputs": {"sequences": [item["id"]], "forward": ["ACGTACGTACGTACGTACGT"],
                   "reverse": ["ACGTACGTACGTACGTACGT"]}})
    assert r.status_code == 200
    kind, title, func, kw = calls[0]
    assert kind == "pcr" and callable(func) and title.startswith("Test primers")
    assert kw["resources"] == ("pcr", 0)  # its own lane: not a compute slot
    assert kw["forward"] == "ACGTACGTACGTACGTACGT" and kw["items"][0]["id"] == item["id"]
    assert kw["folder"] is None


PAIR = {"left": {"sequence": "AAAACCCC", "start": 11, "end": 18, "length": 8, "tm": 60.1, "gc": 50, "penalty": 0.5},
        "right": {"sequence": "TTTTGGGG", "start": 100, "end": 107, "length": 8, "tm": 61.2, "gc": 50, "penalty": 0.5},
        "product_size": 98, "product_start": 11, "product_end": 108, "wraps": False, "penalty": 1.25}


def test_primer_item_and_lists(setup):
    """A primer result is an item; a ticked pair becomes a list of its two primers."""
    files, _ = setup
    item = _items(files, "synthetic_mt.gb")[0]
    data = {"mode": "design", "date": "2026-10-28 12:00",
            "template": {"id": item["id"], "label": item["label"], "length": 100, "circular": False},
            "parameters": {"product_min": 50, "product_max": 200, "tm_min": 57, "tm_opt": 60, "tm_max": 63,
                           "gc_min": 30, "gc_max": 70, "num_return": 5},
            "pairs": [PAIR], "warnings": []}
    new_id = library.add_primers(data, "test primers")
    assert library.get_item(new_id)["kind"] == "primers"
    assert library.get_item(new_id)["mode"] == "design" and library.info(library.get_item(new_id))["mode"] == "design"
    assert library.info(library.get_item(new_id))["pairs"] == 1
    from cladia.server import app
    client = TestClient(app)
    r = client.get(f"/api/primers/{new_id}")
    assert r.status_code == 200 and r.json()["pairs"][0]["product_size"] == 98
    r = client.post(f"/api/primers/{new_id}/sequences", json={"rows": [0]})
    assert r.status_code == 200
    [made] = r.json()["items"]
    made_item = library.get_item(made["id"])
    assert made_item["kind"] == "list"
    assert made_item["label"] == f"{item['label']} pair 1"
    assert [child["label"] for child in library.children(made_item)] == \
        [f"{item['label']} pair 1 › {item['label']}_pair_1_F",
         f"{item['label']} pair 1 › {item['label']}_pair_1_R"]
    assert [str(record.seq) for record in library.records(made_item)] == ["AAAACCCC", "TTTTGGGG"]
    assert "Tm 60.1" in library.records(made_item)[0].description
    # ticked again: a new pair of lists, not a clash
    r = client.post(f"/api/primers/{new_id}/sequences", json={"rows": [0]})
    assert r.json()["items"][0]["id"] != made["id"]
    # the Test tab's sources: each design pair, and the saved pairs (one entry each)
    sources = client.get("/api/primers/sources").json()
    assert sources["designs"][0]["pairs"][0]["forward"] == "AAAACCCC"
    saved = [s for s in sources["lists"] if s["label"] == made_item["label"]]
    assert saved and saved[0]["forward"] == "AAAACCCC" and saved[0]["reverse"] == "TTTTGGGG"
    # primers imported as sequences of their own pair by name; a lone one fills its side only
    client.post("/api/library/upload", json={"filename": "p.fasta", "text": ">cox1 F\nACGTACGTAC\n>cox1 R\nGGGGCCCCAA\n>rbcL_fwd\nTTTTAAAACC\n"})
    lists = {s["label"]: s for s in client.get("/api/primers/sources").json()["lists"]}
    pair = next(s for label, s in lists.items() if label.endswith("cox1"))
    assert pair["forward"] == "ACGTACGTAC" and pair["reverse"] == "GGGGCCCCAA"
    lone = next(s for label, s in lists.items() if "rbcL" in label)
    assert lone["forward"] == "TTTTAAAACC" and "reverse" not in lone and lone["label"].endswith("(forward only)")
    # a table isn't a record: the record export refuses it
    r = client.post("/api/export", json={"ids": [new_id], "format": "genbank"})
    assert r.status_code == 400


# ── MAFFT ────────────────────────────────────────────────────────────────

def test_mafft_options(setup, tmp_path):
    files, calls = setup
    job = {"log": "", "outputs": []}
    owner_mafft.run_alignment(job, tmp_path, _items(files, "set.fasta"), "out", "einsi", False,
                   max_iterate=5, gap_open=2.0, gap_extend=0.1, leave_gappy=True)
    args, _ = calls[0]
    assert args[:8] == ["mafft", "--thread", "1", "--genafpair", "--ep", "0.1", "--maxiterate", "5"]
    assert args[8:] == ["--op", "2.0", "--leavegappyregion", "/data/input.fasta"]
    assert job["outputs"][0]["kind"] == "alignment"


def test_mafft_add_to_alignment(setup, tmp_path):
    files, calls = setup
    aln = _items(files, "aln.fasta")[0]
    assert aln["kind"] == "alignment"
    job = {"log": "", "outputs": []}
    owner_mafft.run_alignment(job, tmp_path, _items(files, "set.fasta")[:1], "out", "auto", False,
                   add_to=aln, fragments=True, keep_length=True)
    args, workdir = calls[0]
    assert args == ["mafft", "--thread", "1", "--auto", "--addfragments", "/data/input.fasta", "--keeplength",
                    "/data/existing.fasta"]
    # strainA is already in the alignment, so the new one is renamed
    assert (workdir / "input.fasta").read_text().startswith(">strainA_\n")
    assert (workdir / "existing.fasta").read_text().count(">") == 2


def test_mafft_align_to_reference(setup, tmp_path):
    files, calls = setup
    reference, other = _items(files, "set.fasta")
    job = {"log": "", "outputs": []}
    owner_mafft.run_alignment(job, tmp_path, [other], "out", "auto", False,
                   add_to=reference, fragments=True, keep_length=True)
    args, workdir = calls[0]
    assert args == ["mafft", "--thread", "1", "--auto", "--addfragments", "/data/input.fasta", "--keeplength",
                    "/data/existing.fasta"]
    assert (workdir / "existing.fasta").read_text() == ">strainA\nACGTACGT\n"
    assert (workdir / "input.fasta").read_text() == ">strainB\nACGTTCGT\n"
    assert job["progress"]["mafft"]["add"] is True
    assert job["progress"]["steps"] == owner_mafft.mafft_steps()
    assert job["progress"]["at"] == 2 and job["progress"]["detail"] == "Saving alignment"


# ── Trees ────────────────────────────────────────────────────────────────

def test_tree_jobs(setup, tmp_path, monkeypatch):
    files, calls = setup
    (files / "four.fasta").write_text(">s one\nACGT-ACGTA\n>s/two\nACGTTACGTA\n>t\nACGA-ACGTA\n>u\nTCGA-ACGTA\n")
    aln = _items(files, "four.fasta")[0]
    assert aln["kind"] == "alignment"

    job = {"log": "", "outputs": [], "id": "j1"}
    owner_fasttree.run_tree(job, tmp_path, aln, "fasttree", "ft")
    args, workdir = calls[-1]
    assert args == ["env", "OMP_NUM_THREADS=1", "fasttreeMP", "-nt", "-gtr", "-gamma", "/data/aln.fasta"]
    # names the tree programs keep as they are
    assert [line for line in (workdir / "aln.fasta").read_text().splitlines() if line.startswith(">")] == [">s", ">s_two", ">t", ">u"]
    assert job["outputs"][0]["kind"] == "tree"

    def fake_iqtree(job, image, args, workdir):
        calls.append((args, workdir))
        (workdir / "run.treefile").write_text("((s,s_two)90:0.1,t,u);\n")
        (workdir / "run.iqtree").write_text("Best-fit model: JC\n")
        return ""

    monkeypatch.setattr(owner_execution, "tool", fake_iqtree)
    job = {"log": "", "outputs": [], "id": "j2"}
    (tmp_path / "iq").mkdir()
    owner_iqtree.run_tree(job, tmp_path / "iq", aln, "iqtree", "iq", outgroup="s/two")
    args, _ = calls[-1]
    assert args == ["iqtree3", "-s", "/data/aln.fasta", "--prefix", "/data/run", "--seed", "1", "-m", "MFP",
                    "-T", "1", "-B", "1000", "-o", "s_two"]
    assert "Best-fit model" in job["log"]
    tree = library.get_item(job["outputs"][0]["id"])
    assert Path(tree["path"]).read_text().startswith("((s,s_two)90")


def _until(test, seconds=5):
    end = time.time() + seconds
    while not test() and time.time() < end:
        time.sleep(0.02)
    return test()


def test_jobs_run_side_by_side(setup, monkeypatch):
    """Lanes and the core budget: one-core jobs together, a whole-budget job alone and
    not overtaken, downloads beside them; a cancelled waiting job never runs."""
    monkeypatch.setattr(jobs, "JOBS", {})
    monkeypatch.setattr(jobs, "CORES", 3)
    release = threading.Event()
    started = []

    def work(job, workdir):
        started.append(job["title"])
        release.wait(5)

    orfs = [jobs.submit("orfs", f"orfs{i}", work) for i in range(3)]  # 3 x 1 core: all at once
    tree = jobs.submit("tree", "tree", work)                          # the whole budget: waits
    late = jobs.submit("orfs", "late", work)                          # behind the tree, though 1 core
    dl = jobs.submit("download", "download", work)                    # its own lane: at once
    gone = jobs.submit("orfs", "gone", work)
    assert _until(lambda: len(started) == 4)
    assert sorted(started) == ["download", "orfs0", "orfs1", "orfs2"] and tree["status"] == "queued"
    assert orfs[0]["cores"] == 1
    jobs.cancel(gone["id"])
    assert gone["status"] == "cancelled" and (config.APP_DIR / "logs" / f"{gone['id']}.json").exists()
    release.set()
    assert _until(lambda: all(j["status"] in ("done", "cancelled") for j in jobs.JOBS.values()))
    assert started.index("tree") < started.index("late") and "gone" not in started
    assert tree["cores"] == 3  # the whole budget, given when it started


# ── Saved logs ───────────────────────────────────────────────────────────

def test_logs_are_saved_and_listed(setup, monkeypatch):
    from cladia.server import app

    def works(job, workdir):
        job["log"] += "all fine\n"
        (workdir / "run.log").write_text("program's own log\n")
        job["live_log"] = str(workdir / "run.log")

    def breaks(job, workdir):
        job["log"] += "trying\n"
        raise RuntimeError("it broke")

    monkeypatch.setattr(jobs, "JOBS", {})
    ok = {"id": "20260101-000000-aaaa", "kind": "test", "title": "Works", "status": "queued", "log": "",
          "outputs": [], "error": None, "created": 1.0}
    bad = {**ok, "id": "20260102-000000-bbbb", "title": "Breaks", "outputs": [], "created": 2.0}
    queued = {**ok, "id": "20260103-000000-cccc", "title": "Cancelled", "outputs": [], "created": 3.0}
    for job, func in ((ok, works), (bad, breaks)):
        jobs.JOBS[job["id"]] = job
        jobs._run(job, func, {})
    jobs.JOBS[queued["id"]] = queued  # cancelled while waiting: logged, never run
    jobs._queue.append((queued, works, {}))
    jobs.cancel(queued["id"])
    saved = {p.stem for p in (config.APP_DIR / "logs").glob("*.json")}
    assert saved == {ok["id"], bad["id"], queued["id"]}
    assert not (config.APP_DIR / "jobs" / ok["id"]).exists()  # done: its folder is gone
    assert (config.APP_DIR / "jobs" / bad["id"]).exists()     # failed: kept until the next start

    # This session's jobs, without their log texts
    client = TestClient(app)
    listed = client.get("/api/jobs").json()
    assert [j["title"] for j in listed] == ["Cancelled", "Breaks", "Works"] and "log" not in listed[0]
    assert [j["status"] for j in listed] == ["cancelled", "failed", "done"]

    # A new session: the jobs list starts empty, the archive has them all
    monkeypatch.setattr(jobs, "JOBS", {})
    assert client.get("/api/jobs").json() == []
    archive = client.get("/api/logs").json()
    assert [j["title"] for j in archive] == ["Cancelled", "Breaks", "Works"] and "log" not in archive[0]
    log = client.get(f"/api/logs/{ok['id']}").json()
    assert "all fine" in log["log"] and "program's own log" in log["log"] and log["finished"] >= log["started"]
    assert client.get(f"/api/logs/{bad['id']}").json()["error"] == "it broke"
    assert client.get("/api/logs/..%2Fitems").status_code == 404

    # Deleting some: gone from the archive; a running job's log is kept
    jobs.JOBS["20260104-000000-dddd"] = {**ok, "id": "20260104-000000-dddd", "status": "running"}
    r = client.post("/api/logs/delete", json={"ids": [ok["id"], bad["id"], "20260104-000000-dddd", "../items"]})
    assert r.json() == {"removed": 2}
    assert [j["title"] for j in client.get("/api/logs").json()] == ["Cancelled"]


# ── BLAST at NCBI (the web requests replaced) ────────────────────────────

def _blast_json():
    hsp = {"bit_score": 80.5, "evalue": 1e-20, "identity": 45, "align_len": 50, "gaps": 1,
           "query_from": 1, "query_to": 50, "query_strand": "Plus", "hit_from": 200, "hit_to": 151,
           "hit_strand": "Minus", "qseq": "A" * 50, "hseq": "A" * 50, "midline": "|" * 50}
    second = {**hsp, "bit_score": 20.0, "evalue": 0.5, "query_from": 61, "query_to": 80}
    return {"BlastOutput2": [{"report": {"results": {"search": {"query_len": 100, "hits": [
        {"description": [{"accession": "AB000001", "title": "Chondrus crispus mitochondrion", "sciname": "Chondrus crispus",
                          "taxid": 2769}], "len": 25000, "hsps": [hsp, second]}]}}}}]}


def test_blast_job(setup, monkeypatch, tmp_path):
    asked = []

    def fake(params=None, data=None, timeout=120):
        asked.append(params or data)
        if data:
            return "    RID = ABC123\n    RTOE = 12\n"
        if params.get("FORMAT_OBJECT") == "SearchInfo":
            return "Status=WAITING" if len(asked) == 2 else "Status=READY"
        return json.dumps(_blast_json())

    monkeypatch.setattr(owner_ncbi_blast, "_blast_request", fake)
    monkeypatch.setattr(owner_ncbi_blast, "_wait", lambda job, seconds: None)
    job = {"id": "j1", "log": "", "outputs": []}
    owner_ncbi_blast.run_search(job, tmp_path, {"name": "q", "sequence": "ACGT" * 25, "item": "x", "start": 1, "end": 100},
                   "megablast", "core_nt", entrez="Rhodophyta[organism]")
    assert asked[0]["MEGABLAST"] == "on" and asked[0]["ENTREZ_QUERY"] == "Rhodophyta[organism]"
    (out,) = job["outputs"]
    assert (out["kind"], out["label"]) == ("blast", "q BLAST")
    (hit,) = library.blast_data(library.get_item(out["id"]))["hits"]
    assert (hit["organism"], hit["max_score"], hit["total_score"], hit["cover"], hit["identity"]) == \
        ("Chondrus crispus", 80.5, 100.5, 70, 90.0)
    assert hit["evalue"] == 1e-20 and "hsps" not in hit

    # a refused search says why
    monkeypatch.setattr(owner_ncbi_blast, "_blast_request", lambda **k: '<p class="error">Query too short</p>')
    with pytest.raises(RuntimeError, match="Query too short"):
        owner_ncbi_blast.run_search({"id": "j2", "log": "", "outputs": []}, tmp_path, {"name": "q", "sequence": "A"}, "blastn", "nt")


def test_blast_query_names(setup, monkeypatch):
    from cladia.server import app
    files, _ = setup
    client = TestClient(app)
    (item,) = library.import_paths([files / "synthetic_mt.gb"])[0]
    titles = []
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **kw: titles.append(kw["query"]["name"]) or {})
    queries = [{"item": item, "sequence": "ACGTACGTAC", "start": 11, "end": 20},  # a part: its range
               {"item": item, "sequence": "ACGTACGTAC"}]                         # the whole row
    client.post("/api/tool-registry/run", json={"operation": "blast.search", "engine": "ncbi_blast",
        "inputs": {"queries": queries}})
    name = library.get_item(item)["name"]
    assert titles == [f"{name} 11-20", name]


def test_remove_annotations_and_replace(setup):
    from cladia.server import app
    files, _ = setup
    client = TestClient(app)
    (gid,), _ = library.import_paths([files / "synthetic_mt.gb"])
    (lid,), _ = library.import_paths([files / "set.fasta"])
    assert library.get_item(gid)["features"] > 0
    removed = client.post("/api/library/remove-annotations", json={"ids": [gid, lid]}).json()["removed"]
    record = library.records(library.get_item(gid))[0]
    assert removed > 0 and all(f.type == "source" for f in record.features) and library.get_item(gid)["features"] == 0


def test_settings(setup, monkeypatch):
    from cladia import ncbi
    from cladia.server import app
    client = TestClient(app)
    assert client.get("/api/settings").json()["cores"] == 0
    saved = client.put("/api/settings", json={"cores": 2, "ncbi_email": " me@usc.gal ", "ncbi_api_key": "abc", "x": 1}).json()
    assert saved == {"cores": 2, "ncbi_email": "me@usc.gal", "ncbi_api_key": "abc", "blast_databases": [],
                     "text_size": 100, "submitters": [], "tree_layout": None, "library_view": None}
    assert client.put("/api/settings", json={"text_size": 500}).json()["text_size"] == 200  # kept sensible
    assert jobs.budget() == 2 and ncbi.identity() == {"tool": "Cladia", "email": "me@usc.gal", "api_key": "abc"}
    assert ncbi.pause() == ncbi.KEY_GAP
    client.put("/api/settings", json={"cores": 0, "ncbi_api_key": ""})
    assert jobs.budget() == jobs.CORES and ncbi.pause() == ncbi.GAP
    # old logs go, this session's stay
    jobs.logs_dir().mkdir(parents=True, exist_ok=True)
    old, new = jobs.logs_dir() / "old.json", jobs.logs_dir() / "new.json"
    old.write_text("{}"); new.write_text("{}")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    assert client.post("/api/settings/clean", json={"what": "logs"}).json() == {"removed": 1}
    assert not old.exists() and new.exists()


def test_progress_from_live_logs(tmp_path):
    job = {"status": "running", "live_log": str(tmp_path / "run.log")}
    owner_job_progress.steps(job, "Choosing a model", "Searching trees", "Finishing")
    owner_job_progress.watch_log(job, tmp_path / "run.log", owner_iqtree._watch_iqtree)
    log = tmp_path / "run.log"
    log.write_text("Measuring multi-threading efficiency up to 4 CPU cores\n")
    assert owner_job_progress.current_progress(job)["detail"] == "finding the best number of threads"
    log.write_text("ModelFinder will test up to 4 DNA models (sample size: 3000) ...\n No. Model  -LnL  df  AIC\n"
                   "  1  JC            12177.025    21  24396.050\n  2  JC+I          12176.886    22  24397.771\n")
    p = owner_job_progress.current_progress(job)
    assert (p["at"], p["fraction"], p["detail"]) == (0, 0.5, "2 of up to 4 models")
    log.write_text(log.read_text() + "INITIALIZING CANDIDATE TREE SET\nIteration 10 / LogL: -1 / Time: 0h:0m:1s\n")
    assert owner_job_progress.current_progress(job)["detail"] == "iteration 10"
    # mapping: the stages map.sh announces, after what an earlier script wrote
    job = {"status": "running", "live_log": str(log), "watch": "map", "watch_from": len("ends round\n")}
    owner_job_progress.steps(job, "Extending ends", "Mapping reads", "Depth and consensus", "Base counts and clipped reads", "Reading results")
    owner_job_progress.step(job, 1)
    owner_job_progress.watch_log(job, log, owner_minimap2._watch_map, len("ends round\n"))
    log.write_text("ends round\nMapping reads\n[M::main] mapped\nDepth and consensus\n")
    assert owner_job_progress.current_progress(job)["at"] == 2


def test_iqtree_threads():
    # 4 for small and medium alignments; IQ-TREE measures them itself for large ones
    assert owner_iqtree.iqtree_threads(60, 575, 21) == "4"
    assert owner_iqtree.iqtree_threads(60, 575, 2) == "2"
    assert owner_iqtree.iqtree_threads(30, 17821, 21) == "AUTO"
    from Bio.SeqRecord import SeqRecord
    from Bio.Seq import Seq
    assert owner_iqtree.patterns([SeqRecord(Seq("AACG-")), SeqRecord(Seq("AAcG-"))]) == 4


def test_mafft_and_fasttree_progress():
    # MAFFT's stderr as it comes: counters overwritten with \r, two passes for FFT-NS-2
    two = {"steps": owner_mafft.mafft_steps(), "at": 0, "fraction": None, "detail": "",
           "mafft": {"directions": False, "add": False}}
    log = ("Making a distance matrix ..\n    1 / 120 (thread    0)\n  101 / 120 (thread    0)\ndone.\n"
           "Constructing a UPGMA tree (efffree=0) ... \n  100 / 120\ndone.\n"
           "Progressive alignment 1/2... \nSTEP    10 / 119 (thread    1) f\rSTEP     6 / 119 (thread    0) f\r")
    p = owner_mafft._watch_mafft(log, two)
    assert (p["steps"][p["at"]], p["fraction"], p["detail"]) == ("Aligning", 10 / 119, "Aligning · 10 of 119 work units")
    p = owner_mafft._watch_mafft(log + "done.\nMaking a distance matrix from msa.. \n    0 / 120 (thread    0)\n", two)
    assert p["steps"][p["at"]] == "Aligning" and p["detail"].startswith("Distances")
    # --auto: which strategy it took shows in its first stage (all pairs: L-INS-i, refined)
    auto = {"steps": owner_mafft.mafft_steps(), "at": 0, "fraction": None, "detail": "",
            "mafft": {"directions": False, "add": False}}
    p = owner_mafft._watch_mafft("All-to-all alignment.\n   10 / 120 (by thread   2) \n", auto)
    assert p["steps"] == owner_mafft.mafft_steps() and p["at"] == 0
    p = owner_mafft._watch_mafft("All-to-all alignment.\nProgressive alignment ... \n001-0012-1 (thread    1) identical     \r"
                          "002-0003-0 (thread    2) identical     \r", auto)
    assert (p["steps"][p["at"]], p["detail"]) == ("Aligning", "Refining · pass 2")
    # FastTree: its stage, and its last progress line when it belongs to that stage
    log = "Initial topology in 0.5 seconds\n      1.31 seconds: SPR round   1 of   2, 1 of 58 nodes\n"
    assert owner_fasttree._watch_fasttree(log, {}) == {"at": 1, "fraction": None, "detail": "SPR round   1 of   2, 1 of 58 nodes"}
    p = owner_fasttree._watch_fasttree(log + "      5.31 seconds: ML NNI round 1 of 10, 1 of 28 splits\nOptimize all lengths: LogLk = -1\n", {})
    assert (p["at"], p["detail"]) == (3, "")


@pytest.mark.parametrize("filename,adding", [("mafft-auto-progress.log", False), ("mafft-reference-progress.log", True)])
def test_mafft_real_log_progress(filename, adding):
    # Captured from MAFFT 7.526 with 62 synthetic sequences and 12 workers.
    text = (DATA / filename).read_text(encoding="utf-8")
    progress = {"at": 0, "steps": owner_mafft.mafft_steps(), "mafft": {"directions": False, "add": adding}}
    last_at = 0
    for end in [m.end() for m in re.finditer(r"[\r\n]", text)]:
        found = owner_mafft._watch_mafft(text[:end], progress)
        if found is None:
            continue
        assert found["steps"] == progress["steps"]
        assert found["at"] >= last_at
        assert found["fraction"] is None or 0 <= found["fraction"] <= 1
        last_at = found["at"]
    assert last_at == 2
    if adding:
        before = text[:text.index("Combining")]
        assert owner_mafft._watch_mafft(before, progress)["detail"].startswith("Aligning to reference")
        assert owner_mafft._watch_mafft(before + "Combining ..\n0 / 62\n", progress)["detail"] == "Combining results"


def test_mafft_progress_ignores_unrelated_and_impossible_counters():
    progress = {"at": 0, "steps": owner_mafft.mafft_steps(), "mafft": {"directions": False, "add": True}}
    prefix = "All-to-all alignment.\n  0 / 1 (by thread 0)\n"
    log = prefix + "STEP 12 / 62 (thread 1)\rDP 00072 / 00001\rblosum 62 / kimura 200\n"
    found = owner_mafft._watch_mafft(log, progress)
    assert found["at"] == 1 and found["fraction"] == 12 / 62
    assert found["detail"] == "Aligning to reference · 12 of 62 work units"
    found = owner_mafft._watch_mafft(log + "STEP 72 / 62 (thread 11)\rSTEP 9 / 62 (thread 2)\r", progress)
    assert found["detail"] == "Aligning to reference · 12 of 62 work units"
    # A changed denominator starts its own counter group, not 72 of 62.
    found = owner_mafft._watch_mafft(prefix + "STEP 72 / 100\rSTEP 5 / 62\r", progress)
    assert found["detail"] == "Aligning to reference · 5 of 62 work units"
    assert owner_mafft._watch_mafft(prefix + "STEP 72 / 62\r", progress)["fraction"] is None


def test_mauve_progress():
    progress = {"genomes": 3}
    assert owner_mauve._watch_mauve("Storing raw sequence\nSequence loaded successfully.\n", progress)["fraction"] == 1 / 3
    seeds = "Sequence loaded successfully.\n" * 3 + "Using weight 11 mers for initial seeds\n6%..12%..50%.."
    assert owner_mauve._watch_mauve(seeds, progress) == {"at": 1, "fraction": 0.5, "detail": ""}
    pairs = seeds + "done.\nCalculating pairwise breakpoint distances\nPair 0, 1 has 961 initial LCBs\n"
    assert owner_mauve._watch_mauve(pairs, progress)["detail"] == "1 of 3 pairs"
    aligning = pairs + "Aligning...\nAligning node 3 to 4 via 1!\n"
    assert owner_mauve._watch_mauve(aligning, progress) == {"at": 3, "fraction": 0, "detail": "merge 1 of 2"}
