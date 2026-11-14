"""Encodings, formats recognised by content (any file ending) and protein sequences.

Run with:  python -m pytest tests
"""

import codecs
import shutil
from pathlib import Path

import pytest
from Bio import SeqIO
from fastapi.testclient import TestClient

from cladia import config, library

DATA = Path(__file__).parent / "data"
NOTE = 'FEATURES             Location/Qualifiers\n     misc_feature    1..10\n                     /note="{}"\n'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    files = tmp_path / "my files"
    files.mkdir()
    from cladia.server import app
    return files, TestClient(app)


def _add(client, *paths):
    r = client.post("/api/library/add", json={"paths": [str(p) for p in paths]}).json()
    assert r["errors"] == [], r["errors"]
    by_id = {it["id"]: it for it in client.get("/api/library").json()}
    return [by_id[i] for i in r["ids"]]


def _genbank_with_note(note):
    text = (DATA / "synthetic_mt.gb").read_text()
    return text.replace("FEATURES             Location/Qualifiers\n", NOTE.format(note), 1)


# ── Encodings ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_accents_in_any_encoding(setup, encoding):
    """UTF-8 (with or without a BOM) and old Windows files read the same; the
    library copy is plain UTF-8."""
    files, client = setup
    (files / "acc.gb").write_bytes(_genbank_with_note("Pazo de Mariñán, Á Coruña").encode(encoding))
    [item] = _add(client, files / "acc.gb")
    notes = [f["notes"].get("note") for f in client.get(f"/api/sequences/{item['id']}").json()["features"]]
    assert ["Pazo de Mariñán, Á Coruña"] in notes
    stored = Path(item["path"]).read_bytes()
    assert not stored.startswith(codecs.BOM_UTF8) and "Mariñán".encode("utf-8") in stored


def test_bom_and_utf16_fasta(setup):
    files, client = setup
    (files / "bom.fasta").write_bytes(codecs.BOM_UTF8 + b">a\nACGT\n>b\nACGG\n")
    (files / "u16.fasta").write_text(">x first\nACGTACGT\n", encoding="utf-16")
    lst, single = _add(client, files / "bom.fasta", files / "u16.fasta")
    assert lst["kind"] == "list" and lst["count"] == 2
    assert client.get(f"/api/sequences/{single['id']}").json()["sequence"] == "ACGTACGT"


def test_bom_genbank_list_counts_every_record(setup):
    files, client = setup
    one = (DATA / "synthetic_mt.gb").read_text()
    (files / "two.gb").write_bytes(codecs.BOM_UTF8 + (one + one.replace("LOCUS       Mt", "LOCUS       Mt2", 1)).encode())
    [lst] = _add(client, files / "two.gb")
    assert lst["count"] == 2


def test_save_keeps_characters_outside_windows_1252(setup):
    files, client = setup
    shutil.copy(DATA / "synthetic_mt.gb", files / "mt.gb")
    [item] = _add(client, files / "mt.gb")
    data = client.get(f"/api/sequences/{item['id']}").json()
    data["features"][0]["notes"]["note"] = ["α-helix, 测试"]
    assert client.put(f"/api/sequences/{item['id']}", json=data).status_code == 200
    again = client.get(f"/api/sequences/{item['id']}").json()
    assert again["features"][0]["notes"]["note"] == ["α-helix, 测试"]


# ── Formats by content ───────────────────────────────────────────────────

def test_any_fasta_ending_and_clustal(setup):
    files, client = setup
    (files / "prot.faa").write_text(">p1 kinase\nMKVLAAGIVGLLLAQWERTY\n>p2\nMKVLSAGIVGLLLAQWERTF\n")
    (files / "seqs.txt").write_text(">s1\nACGTACGTAA\n")
    (files / "aligned.afa").write_text(">a\nMKV-LAA\n>b\nMKVQLAA\n")
    (files / "clustal.aln").write_text(
        "CLUSTAL W (1.83) multiple sequence alignment\n\n"
        "seqA            MKV-LAAGIV\nseqB            MKVQLAAGIV\n                ***:******\n")
    prot, txt, afa, aln = _add(client, files / "prot.faa", files / "seqs.txt", files / "aligned.afa", files / "clustal.aln")
    assert (prot["kind"], txt["kind"], afa["kind"], aln["kind"]) == ("list", "sequence", "alignment", "alignment")
    assert [{k: r[k] for k in ("name", "sequence")} for r in client.get(f"/api/alignment/{aln['id']}").json()] == [
        {"name": "seqA", "sequence": "MKV-LAAGIV"}, {"name": "seqB", "sequence": "MKVQLAAGIV"}]
    (files / "notes.txt").write_text("hello")
    r = client.post("/api/library/add", json={"paths": [str(files / "notes.txt")]}).json()
    assert r["ids"] == [] and "not a GenBank, GFF3, FASTA, Clustal, Newick or GFA" in r["errors"][0]


# ── Proteins ─────────────────────────────────────────────────────────────

def test_protein_sequences(setup):
    files, client = setup
    (files / "p.faa").write_text(">kin1 a kinase\nMKVLAAGIVGLLLAQWERTYHKD\n")
    (files / "d.fasta").write_text(">dna1\nACGTTGCANNACGTRYACGT\n")
    prot, dna = _add(client, files / "p.faa", files / "d.fasta")
    pdata = client.get(f"/api/sequences/{prot['id']}").json()
    assert pdata["protein"] is True and client.get(f"/api/sequences/{dna['id']}").json()["protein"] is False
    assert " aa " in Path(prot["path"]).read_text().splitlines()[0]  # GenBank LOCUS line: amino acids
    # saved and read back: still a protein
    assert client.put(f"/api/sequences/{prot['id']}", json=pdata).status_code == 200
    assert SeqIO.read(prot["path"], "genbank").annotations["molecule_type"] == "protein"
    # the DNA tools say no
    r = client.post("/api/tool-registry/run", json={"operation": "annotate.denovo", "engine": "mfannot",
        "inputs": {"sequences": [prot["id"]]}, "options": {"organelle": "mitochondrion"}})
    assert r.status_code == 400 and "protein" in r.json()["detail"]
    r = client.post("/api/tool-registry/run", json={"operation": "map.reads", "engine": "minimap2",
        "inputs": {"reference": [prot["id"]], "read_files": [str(files / "d.fasta")]}})
    assert r.status_code == 400 and "protein" in r.json()["detail"]


def test_genetic_code_saved_on_the_cds(setup):
    """A new /transl_table (the sequence's genetic code) gets the CDS translated again."""
    files, client = setup
    from Bio.Seq import Seq
    from Bio.SeqFeature import SeqFeature, SimpleLocation
    from Bio.SeqRecord import SeqRecord
    rec = SeqRecord(Seq("ATGTGATGGAAATAA"), id="tga", name="tga", annotations={"molecule_type": "DNA"},
                    features=[SeqFeature(SimpleLocation(0, 15, 1), type="CDS",
                                         qualifiers={"gene": ["g"], "translation": ["M"]})])
    SeqIO.write(rec, files / "tga.gb", "genbank")
    [item] = _add(client, files / "tga.gb")
    data = client.get(f"/api/sequences/{item['id']}").json()
    assert data["geneticCode"] == 1
    data["features"][0]["notes"]["transl_table"] = ["4"]
    assert client.put(f"/api/sequences/{item['id']}", json=data).status_code == 200
    cds = next(f for f in SeqIO.read(item["path"], "genbank").features if f.type == "CDS")
    assert cds.qualifiers["transl_table"] == ["4"] and cds.qualifiers["translation"] == ["MWWK"]  # TGA = W in code 4
    assert client.get(f"/api/sequences/{item['id']}").json()["geneticCode"] == 4


def test_header_facts_and_info(setup):
    """Description and organism from GenBank headers and FASTA headers; the hover card's info."""
    files, client = setup
    one = (DATA / "synthetic_mt.gb").read_text()
    head = "DEFINITION  Chondrus crispus mitochondrion, complete\n            genome.\nACCESSION   NC_001677\n"
    org = "SOURCE      Chondrus crispus\n  ORGANISM  Chondrus crispus\n            Eukaryota; Rhodophyta.\n"
    two = one.replace("FEATURES", head + org + "FEATURES", 1)
    (files / "pair.gb").write_text(two + one.replace("LOCUS       Mt", "LOCUS       Mt2", 1))
    (files / "rows.fasta").write_text(">r1 atp1 gene [Ulva lactuca]\nAC-GT\n>r2\nACCGT\n")
    lst, aln = _add(client, files / "pair.gb", files / "rows.fasta")
    kids = sorted((it for it in client.get("/api/library").json() if it["parent"] == lst["id"]), key=lambda it: it["record"])
    assert kids[0]["description"] == "Chondrus crispus mitochondrion, complete genome"
    assert kids[0]["organism"] == "Chondrus crispus" and kids[1]["organism"] == ""
    rows = client.get(f"/api/alignment/{aln['id']}").json()
    assert (rows[0]["description"], rows[0]["organism"]) == ("atp1 gene", "Ulva lactuca")
    card = client.get(f"/api/info/{lst['id']}").json()
    assert card["count"] == 2 and card["organisms"] == [["Chondrus crispus", 1]] and card["min"] == 15789
    card = client.get(f"/api/info/{kids[0]['id']}").json()
    assert card["accession"] == "NC_001677" and card["cds"] == 8 and card["length"] == 15789
    assert client.get(f"/api/sequences/{kids[0]['id']}").json()["organism"] == "Chondrus crispus"


def test_file_dialog_filters_are_valid():
    """pywebview refuses a filter whose description has e.g. a comma: the Open
    dialog then never opened (Import files did nothing)."""
    util = pytest.importorskip("webview.util")
    from cladia import window
    filters = [*window.FILE_TYPES, *window.READ_TYPES, *window.GFF_TYPES,
               *[f for types in window.EXPORT_TYPES.values() for f in types]]
    for f in filters:
        util.parse_file_type(f)  # raises ValueError for a bad one
