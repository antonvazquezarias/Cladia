import cladia.tool_plugins.genbank_download as owner_genbank_download
"""More import formats (gzip, EMBL, NEXUS, PHYLIP, Stockholm, trees with comments),
NEXUS/PHYLIP export, and downloading from GenBank (NCBI replaced)."""

import gzip
import io
from io import StringIO
from pathlib import Path

import pytest
from Bio import AlignIO, Phylo, SeqIO

from cladia import config, jobs, library

DATA = Path(__file__).parent / "data"

MRBAYES = """#NEXUS
begin trees;
   translate
      1 Alpha,
      2 Beta,
      3 'Gamma gamma',
      4 Delta
      ;
   tree con_50_majrule = [&U] (1[&prob=1.0]:0.1[&length_mean=0.1],(2[&prob=1.0]:0.2,3[&prob=1.0]:0.3)[&prob=9.7e-01,prob(percent)="97",prob_range={0.9,1.0}]:0.05,4[&prob=1.0]:0.4)[&prob=1.0];
end;
"""
NEXUS_ALN = """#NEXUS
begin data;
dimensions ntax=3 nchar=8;
format datatype=dna missing=? gap=-;
matrix
s1 ACGT-ACG
s2 ACGTTA?G
s3 ACGTTACG
;
end;
"""


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    return tmp_path


def _import(tmp_path, name, text):
    path = tmp_path / name
    path.write_bytes(text) if isinstance(text, bytes) else path.write_text(text)
    ids, errors = library.import_paths([path])
    return [library.get_item(i) for i in ids], errors


def test_gzip_and_embl(lib):
    record = SeqIO.read(DATA / "synthetic_mt.gb", "genbank")
    buffer = StringIO()
    SeqIO.write(record, buffer, "embl")
    (item,), errors = _import(lib, "mt.embl.gz", gzip.compress(buffer.getvalue().encode()))
    assert not errors and item["kind"] == "sequence"
    stored = library.records(item)[0]
    assert len(stored) == len(record) and len(stored.features) == len(record.features)
    assert Path(item["path"]).read_text().startswith("LOCUS")  # stored as GenBank
    # a gzipped GenBank is stored uncompressed
    (item,), _ = _import(lib, "copy.gb.gz", gzip.compress((DATA / "synthetic_mt.gb").read_bytes()))
    assert Path(item["path"]).read_text().startswith("LOCUS")


def test_alignments(lib):
    (aln,), errors = _import(lib, "a.nex", NEXUS_ALN)
    assert not errors and aln["kind"] == "alignment"
    rows = library.alignment_rows(aln)
    assert [r["sequence"] for r in rows] == ["ACGT-ACG", "ACGTTA-G", "ACGTTACG"]  # missing data: a gap
    phylip = " 2 12\nseq_one   ACGT-ACG\nseq_two   ACGTTACG\n\nAAAA\nCCCC\n"  # interleaved
    (aln,), _ = _import(lib, "a.phy", phylip)
    assert [r["sequence"] for r in library.alignment_rows(aln)] == ["ACGT-ACGAAAA", "ACGTTACGCCCC"]
    sequential = " 2 12\nseq_one   ACGT-ACGAAAA\nseq_two   ACGTTACGCCCC\n"
    (aln,), _ = _import(lib, "b.phy", sequential)
    assert [r["name"] for r in library.alignment_rows(aln)] == ["seq_one", "seq_two"]
    sto = "# STOCKHOLM 1.0\nseqA/1-8  ACGU-ACG\nseqB/2-9  ACGUUACG\n#=GC SS_cons <<..>>..\n//\n"
    (aln,), _ = _import(lib, "r.sto", sto)
    assert [r["name"] for r in library.alignment_rows(aln)] == ["seqA/1-8", "seqB/2-9"]


def test_nexus_trees(lib):
    (tree,), errors = _import(lib, "mb.con.tre", MRBAYES)
    assert not errors and tree["kind"] == "tree"
    nodes = library.tree_nodes(tree)
    assert sorted(n["name"] for n in nodes if n["name"]) == ["Alpha", "Beta", "Delta", "Gamma gamma"]
    assert [n["support"] for n in nodes if n["name"] is None and n["parent"] >= 0] == ["0.97"]
    # alignment and tree in one NEXUS file: two items
    items, _ = _import(lib, "both.nex", NEXUS_ALN + MRBAYES.replace("#NEXUS\n", ""))
    assert sorted(it["kind"] for it in items) == ["alignment", "tree"]
    # BEAST-style comments in a plain Newick file
    (tree,), _ = _import(lib, "beast.tree", "((A:0.1,B:0.2)[&posterior=0.9876]:0.05,C:0.3);\n")
    assert [n["support"] for n in library.tree_nodes(tree) if n["name"] is None and n["parent"] >= 0] == ["0.99"]
    # many trees in one file: refused with a reason
    items, errors = _import(lib, "boot.ufboot", "(A,(B,C));\n((A,B),C);\n(A,(B,C));\n")
    assert not items and "3 trees in one file" in errors[0]


def test_export_nexus_phylip(lib):
    (aln,), _ = _import(lib, "a.nex", NEXUS_ALN)
    nexus = library.export_alignment(aln, "nexus")
    assert [str(r.seq) for r in AlignIO.read(StringIO(nexus), "nexus")][1] == "ACGTTA-G"
    phylip = library.export_alignment(aln, "phylip")
    assert phylip.split()[:2] == ["3", "8"] and len(AlignIO.read(StringIO(phylip), "phylip-relaxed")) == 3
    (tree,), _ = _import(lib, "mb.con.tre", MRBAYES)
    text = library.export_trees([tree], "nexus")
    assert text.startswith("#NEXUS") and Phylo.read(StringIO(text), "nexus").count_terminals() == 4


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_download_job(lib, monkeypatch):
    record = SeqIO.read(DATA / "synthetic_mt.gb", "genbank")
    record.id, record.name, record.annotations["accessions"] = "MK000001.1", "MK000001", ["MK000001"]
    second = record[:100]
    second.id, second.name, second.annotations = "MK000002.1", "MK000002", dict(record.annotations, accessions=["MK000002"])
    asked = []

    def fake_urlopen(url, data, timeout):
        asked.append(data.decode())
        buffer = StringIO()
        SeqIO.write([record, second], buffer, "genbank")
        return FakeResponse(buffer.getvalue().encode())

    from cladia import ncbi
    monkeypatch.setattr(ncbi.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ncbi.time, "sleep", lambda s: None)
    job = {"id": "d", "log": "", "outputs": []}
    (lib / "w").mkdir()
    owner_genbank_download.run_download(job, lib / "w", ["MK000001", "MK000002.1", "XX999999"], "my plastomes", True)
    assert "db=nuccore" in asked[0] and "MK000001%2CMK000002.1%2CXX999999" in asked[0]
    (out,) = job["outputs"]
    item = library.get_item(out["id"])
    assert (item["kind"], item["name"], item["count"]) == ("list", "my plastomes", 2)
    assert "Not found in GenBank (nucleotide): XX999999" in job["log"]
    # separate sequences
    job = {"id": "e", "log": "", "outputs": []}
    (lib / "w2").mkdir()
    owner_genbank_download.run_download(job, lib / "w2", ["MK000001", "MK000002"], "x", False)
    assert [o["label"] for o in job["outputs"]] == ["MK000001", "MK000002"]


def test_download_request_checks_accessions(lib):
    from fastapi.testclient import TestClient

    from cladia.server import app
    client = TestClient(app)
    r = client.post("/api/tool-registry/run", json={"operation": "download.accessions", "engine": "genbank_download",
        "inputs": {"accessions": ["NC_001677, not an accession!"]}})
    assert r.status_code == 400 and "accession!" in r.json()["detail"]
    r = client.post("/api/tool-registry/run", json={"operation": "download.accessions", "engine": "genbank_download",
        "inputs": {"accessions": ["  "]}})
    assert r.status_code == 400


def test_ncbi_search(monkeypatch):
    import json

    from cladia import ncbi
    calls = []

    def fake_urlopen(url, data, timeout):
        calls.append((url.rsplit("/", 1)[1], data.decode()))
        if url.endswith("esearch.fcgi"):
            reply = {"esearchresult": {"count": "250", "idlist": ["11", "12"]}}
        else:
            reply = {"result": {"uids": ["11", "12"],
                                "11": {"accessionversion": "NC_016735.1", "title": "Fucus chloroplast", "organism": "Fucus vesiculosus",
                                       "slen": 124986, "updatedate": "2023/04/03"},
                                "12": {"caption": "MG922856", "title": "Fucus mito", "organism": "Fucus spiralis", "slen": 36396,
                                       "createdate": "2018/04/24"}}}
        return FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr(ncbi.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ncbi, "GAP", 0)
    r = ncbi.search("Fucus[organism] AND complete genome", start=100)
    assert calls[0][0] == "esearch.fcgi" and "retstart=100" in calls[0][1] and "Fucus%5Borganism%5D" in calls[0][1]
    assert calls[1][0] == "esummary.fcgi" and "id=11%2C12" in calls[1][1]
    assert r["count"] == 250 and r["start"] == 100
    assert [(x["accession"], x["length"], x["date"]) for x in r["results"]] == [
        ("NC_016735.1", 124986, "2023/04/03"), ("MG922856", 36396, "2018/04/24")]


def test_ncbi_server_errors_are_retried(monkeypatch):
    import urllib.error
    from io import BytesIO

    import pytest

    from cladia import ncbi
    page = b"<html><head><title>NCBI/eutils202 - WWW Error 500 Diagnostic</title></head><body>Server</body></html>"
    failures = [500, 502]

    def flaky(url, data, timeout):
        if failures:
            raise urllib.error.HTTPError(url, failures.pop(0), "Internal Server Error", {}, BytesIO(page))
        return FakeResponse(b"ok")

    monkeypatch.setattr(ncbi.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(ncbi.time, "sleep", lambda s: None)
    assert ncbi.post(ncbi.EUTILS + "/efetch.fcgi", {"id": "1"}) == "ok"  # two brief errors, then success
    failures[:] = [500] * 10
    with pytest.raises(ValueError) as e:
        ncbi.post(ncbi.EUTILS + "/efetch.fcgi", {"id": "1"})
    assert "server had an error (500)" in str(e.value) and "<" not in str(e.value)
    failures[:] = [400] * 10  # a bad request isn't retried; its HTML is reduced to text
    with pytest.raises(ValueError) as e:
        ncbi.post(ncbi.EUTILS + "/efetch.fcgi", {"id": "1"})
    assert str(e.value).startswith("NCBI said 400: NCBI/eutils202") and len(failures) == 9
