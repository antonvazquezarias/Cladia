from cladia import gff, graph
from cladia.genbank import sniff

GTF = "\n".join([
    'chr1\tx\tgene\t1\t30\t.\t+\t.\tgene_id "g1"; gene_name "cox1";',
    'chr1\tx\ttranscript\t1\t30\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
    'chr1\tx\texon\t1\t10\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
    'chr1\tx\texon\t21\t30\t.\t+\t.\tgene_id "g1"; transcript_id "t1";',
    'chr1\tx\tCDS\t1\t10\t.\t+\t0\tgene_id "g1"; transcript_id "t1";',
    'chr1\tx\tCDS\t21\t29\t.\t+\t1\tgene_id "g1"; transcript_id "t1";',
    'chr1\tx\tstop_codon\t30\t30\t.\t+\t0\tgene_id "g1"; transcript_id "t1";',
]) + "\n"


def test_gtf_is_read_like_gff3():
    features, fasta = gff.read_gff3(GTF)
    assert fasta == "" and sniff(GTF) is None
    by_type = {f["type"]: f for f in features["chr1"]}
    assert set(by_type) == {"gene", "mRNA", "exon", "CDS"}
    assert by_type["CDS"]["parts"] == [(0, 9), (20, 28)] and by_type["exon"]["parts"] == [(0, 9), (20, 29)]
    assert by_type["CDS"]["qualifiers"]["gene"] == ["cox1"]
    assert gff.is_gtf(GTF) and not gff.is_gtf("##gff-version 3\nc\tx\tgene\t1\t5\t.\t+\t.\tID=g;Name=a\n")


def test_fastg_becomes_gfa():
    a, b = "ACGTACGTAAGGCCTTAGCATTGC", "CCTTAGCATTGCTTTGGGAAACCC"  # a's last 12 bases start b
    rc = lambda s: s[::-1].translate(str.maketrans("ACGT", "TGCA"))
    fastg = (f">EDGE_1_length_{len(a)}_cov_10.5:EDGE_2_length_{len(b)}_cov_4.0;\n{a}\n"
             f">EDGE_1_length_{len(a)}_cov_10.5':EDGE_3_length_5_cov_1.0';\n{rc(a)}\n"
             f">EDGE_2_length_{len(b)}_cov_4.0;\n{b}\n"
             f">EDGE_2_length_{len(b)}_cov_4.0':EDGE_1_length_{len(a)}_cov_10.5';\n{rc(b)}\n")
    assert sniff(fastg) == "fastg"
    g = graph.read_gfa(graph.fastg_to_gfa(fastg))
    assert set(g["nodes"]) == {"EDGE_1", "EDGE_2"} and g["nodes"]["EDGE_1"]["depth"] == 10.5
    assert g["links"] == [("EDGE_1", "+", "EDGE_2", "+", 12)]
    assert sniff(">seq1 desc\nACGT\n") == "fasta"


def test_import_through_the_library(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cladia import config, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    a, b = "ACGTACGTAAGGCCTTAGCATTGC", "CCTTAGCATTGCTTTGGGAAACCC"
    fastg = f">EDGE_1_length_24_cov_9.0:EDGE_2_length_24_cov_3.0;\n{a}\n>EDGE_2_length_24_cov_3.0;\n{b}\n"
    made = client.post("/api/library/upload", json={"filename": "asm.fastg", "text": fastg}).json()
    item = next(i for i in client.get("/api/library").json() if i["id"] == made["ids"][0])
    assert item["kind"] == "graph" and item["path"].endswith(".gfa")
    seq = client.post("/api/library/upload", json={"filename": "s.fasta", "text": ">chr1\n" + "ACGT" * 10 + "\n"}).json()
    done = client.post("/api/annotations/import", json={"id": seq["ids"][0], "text": GTF}).json()
    assert "error" not in done and done["results"][0]["added"] >= 3


def test_graph_from_contigs(tmp_path, monkeypatch):
    import random

    from fastapi.testclient import TestClient

    from cladia import config, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    random.seed(3)
    dna = lambda n: "".join(random.choice("ACGT") for _ in range(n))
    k = dna(60)                                   # the shared k bases
    a, b = dna(300) + k, k + dna(300)             # a -> b
    circle = dna(400)
    fasta = "".join(f">{n}\n{s}\n" for n, s in [("k141_1.fa", a), ("k141_2.fa", b),
                                                  ("k141_3.fa", circle + circle[:60])])
    ids = client.post("/api/library/upload", json={"filename": "c.fasta", "text": fasta}).json()["ids"]
    made = client.post("/api/graph/from-contigs", json={"ids": ids, "name": "g"}).json()
    assert (made["nodes"], made["links"], made["loops"]) == (3, 2, 1)
    layout = client.get(f"/api/graph/{made['id']}/layout").json()
    assert layout["groups"]


def test_circularize_a_contig_whose_end_repeats_its_start(tmp_path, monkeypatch):
    import random

    from fastapi.testclient import TestClient

    from cladia import config, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    random.seed(5)
    dna = lambda n: "".join(random.choice("ACGT") for _ in range(n))
    ring, plain = dna(500), dna(300)
    fasta = f">ring\n{ring + ring[:60]}\n>plain\n{plain}\n"
    ids = client.post("/api/library/upload", json={"filename": "c.fasta", "text": fasta}).json()["ids"]
    done = client.post("/api/library/circularize", json={"ids": ids}).json()
    assert [(d["trimmed"], d["dropped"]) for d in done["done"]] == [(60, 0)] and done["none"] == 1
    items = {i["name"]: i for i in client.get("/api/library").json() if i["kind"] == "sequence"}
    data = client.get(f"/api/sequences/{items['ring']['id']}").json()
    assert data["sequence"] == ring and data["circular"] is True
    again = client.post("/api/library/circularize", json={"ids": ids}).json()
    assert again["done"] == [] and again["circular"] == 1
