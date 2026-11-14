"""Phase 1 formats (docs/PROJECT_REVIEW.md): SnapGene, phyloXML, BED and BLAST tabular output."""
import json
import struct
from pathlib import Path

import pytest

from cladia import config, library, gff
from cladia.genbank import sniff


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    return tmp_path


def _import(tmp_path, name, content):
    path = tmp_path / name
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    ids, errors = library.import_paths([path])
    return [library.get_item(i) for i in ids], errors


# ── SnapGene ─────────────────────────────────────────────────────────────

def _packet(kind, data):
    return struct.pack(">BI", kind, len(data)) + data


def snapgene_file(sequence, circular=True):
    features = ('<Features><Feature name="geneA" type="CDS" directionality="1">'
                '<Segment range="1-6" color="#ff0000" type="standard"/></Feature></Features>')
    return (_packet(9, b"SnapGene" + struct.pack(">HHH", 1, 1, 1)) +
            _packet(0, bytes([1 if circular else 0]) + sequence.encode()) +
            _packet(10, features.encode()))


def test_snapgene_becomes_a_genbank_sequence(lib):
    (item,), errors = _import(lib, "plasmid.dna", snapgene_file("ATGAAATTTGGGCCCAAATAA"))
    assert not errors and item["kind"] == "sequence"
    (record,) = library.records(item)
    assert str(record.seq) == "ATGAAATTTGGGCCCAAATAA" and record.annotations["topology"] == "circular"
    assert [(f.type, int(f.location.start), int(f.location.end)) for f in record.features] == [("CDS", 0, 6)]
    assert Path(item["path"]).read_text().startswith("LOCUS")  # stored as GenBank
    (linear,), _ = _import(lib, "line.dna", snapgene_file("ACGTACGT", circular=False))
    assert library.records(linear)[0].annotations["topology"] == "linear"


def test_a_broken_snapgene_file_is_refused(lib):
    items, errors = _import(lib, "bad.dna", snapgene_file("ACGT")[:20])
    assert not items and "SnapGene" in errors[0]


# ── phyloXML ─────────────────────────────────────────────────────────────

PHYLOXML = """<?xml version="1.0" encoding="UTF-8"?>
<phyloxml xmlns="http://www.phyloxml.org"><phylogeny rooted="true"><clade>
  <clade><confidence type="bootstrap">95</confidence>
    <clade><name>Alpha</name><branch_length>0.1</branch_length></clade>
    <clade><name>Beta</name><branch_length>0.2</branch_length></clade>
  </clade>
  <clade><name>Gamma</name><branch_length>0.3</branch_length></clade>
</clade></phylogeny></phyloxml>"""


def test_phyloxml_becomes_a_tree_with_its_supports(lib):
    assert sniff(PHYLOXML) == "phyloxml"
    (tree,), errors = _import(lib, "t.xml", PHYLOXML)
    assert not errors and tree["kind"] == "tree"
    nodes = library.tree_nodes(tree)
    assert sorted(n["name"] for n in nodes if n["name"]) == ["Alpha", "Beta", "Gamma"]
    assert [n["support"] for n in nodes if n["name"] is None and n["parent"] >= 0] == ["95"]


def test_other_xml_is_not_a_tree(lib):
    assert sniff('<?xml version="1.0"?><svg/>') is None


# ── BLAST tabular ────────────────────────────────────────────────────────

BLAST_TAB = (
    "q1\tNC_001\t99.500\t400\t2\t0\t1\t400\t10\t409\t0.0\t720\n"
    "q1\tNC_001\t90.000\t100\t10\t0\t450\t549\t600\t699\t1e-30\t150\n"
    "q1\tMK_002\t85.000\t300\t45\t0\t1\t300\t1\t300\t2e-50\t200\n"
    "q2\tNC_003\t100.000\t250\t0\t0\t1\t250\t1\t250\t1e-120\t460\n")


def test_blast_tabular_makes_a_results_item_per_query(lib):
    assert sniff(BLAST_TAB) == "blasttab"
    items, errors = _import(lib, "hits.tsv", BLAST_TAB)
    assert not errors and [it["kind"] for it in items] == ["blast", "blast"]
    first = library.blast_data(items[0])
    assert first["query"]["name"] == "q1" and first["imported"] and first["query_length"] is None
    assert [h["accession"] for h in first["hits"]] == ["NC_001", "MK_002"]  # best score first
    best = first["hits"][0]
    assert (best["max_score"], best["total_score"], best["identity"]) == (720.0, 870.0, 99.5)
    assert best["cover"] is None and best["length"] is None  # not in the file
    json.dumps(first)  # (the result is plain JSON)


# ── BED ──────────────────────────────────────────────────────────────────

BED = "track name=genes\nchrA\t10\t40\tgeneX\t0\t+\nchrA\t100\t130\tgeneY\t0\t-\n"
BED12 = "chrA\t0\t100\tspliced\t0\t+\t0\t100\t0\t2\t20,30,\t0,70,\n"


def test_bed_is_converted_to_features_with_one_based_positions():
    assert sniff(BED) == "bed" and gff.is_bed(BED)
    features, _ = gff.read_gff3(BED)
    assert [(f["qualifiers"]["label"], f["strand"], f["parts"]) for f in features["chrA"]] == [
        (["geneX"], 1, [(10, 39)]), (["geneY"], -1, [(100, 129)])]  # BED starts at 0 and ends past the last base
    # BED12: the blocks are the parts of one feature
    (spliced,) = gff.read_gff3(BED12)[0]["chrA"]
    assert spliced["parts"] == [(0, 19), (70, 99)]


def test_bed_goes_onto_a_sequence_and_alone_is_refused(lib):
    (item,), _ = _import(lib, "chrA.fa", ">chrA\n" + "ACGT" * 50 + "\n")
    results, errors = library.import_annotations(BED, library.get_item(item["id"]))
    assert not errors and results[0]["added"] == 2
    record = library.records(library.get_item(item["id"]))[0]
    assert sorted((int(f.location.start), int(f.location.end)) for f in record.features if f.type != "source") == [(10, 40), (100, 130)]
    items, errors = _import(lib, "x.bed", BED)
    assert not items and "BED annotations without sequences" in errors[0]


# -- Protein properties ---------------------------------------------------

def test_protein_properties(lib):
    from fastapi.testclient import TestClient

    from cladia.server import app
    client = TestClient(app)
    protein = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGLYTHMKALRPDEDRL"
    data = client.post("/api/protein/properties", json={"sequence": protein + "*XX-", "window": 9}).json()
    assert data["length"] == len(protein) + 2 and data["analysed"] == len(protein) and data["excluded"] == {"X": 2}
    assert 108 * 100 < data["weight"] < 108 * 130 and 4 < data["pI"] < 11  # about 110 Da a residue
    assert len(data["hydrophobicity"]["values"]) == len(protein) - 8 and data["hydrophobicity"]["positions"][0] == 5
    assert abs(sum(data["composition"].values()) - 100) < 0.5
    assert client.post("/api/protein/properties", json={"sequence": "MK", "window": 9}).json()["hydrophobicity"]["values"] == []
    assert client.post("/api/protein/properties", json={"sequence": "M"}).status_code == 400


def test_dot_plot_figures_keep_their_two_sequences(lib):
    (seq,), _ = _import(lib, "mt.fa", ">mt\n" + "ACGTTGCA" * 40 + "\n")
    (prot,), _ = _import(lib, "p.fa", ">p\nMKLVWQERTYIPASDFGHKLMNPQRST\n")
    part = {"a": {"item": seq["id"]}, "b": {"item": seq["id"]}}
    fid = library.add_figure(library.get_item(seq["id"]), of="dotplot", part=part, name="mt dot plot")
    figure = library.get_item(fid)
    data = library.figure_data(figure)
    assert data["of"] == "dotplot" and data["part"] == part
    library.save_figure_settings(figure, {"word": 10})  # the kind survives a save
    assert library.figure_data(library.get_item(fid))["of"] == "dotplot"
    with pytest.raises(ValueError):
        library.add_figure(library.get_item(prot["id"]), of="dotplot", part=part)
    with pytest.raises(ValueError):
        library.add_figure(library.get_item(seq["id"]), of="dotplot", part={"a": part["a"]})

BEDGRAPH = 'track type=bedGraph name="cov"\nmt\t0\t100\t5\nmt\t100\t200\t-2.5\n'
WIG = "variableStep chrom=mt span=10\n1 3\n51 4\nfixedStep chrom=other start=1 step=5\n1\n2\n"


def test_bedgraph_and_wig_are_read_as_tracks():
    from cladia import tracks
    assert sniff(BEDGRAPH) == "bedgraph" and sniff(WIG) == "wig" and sniff(BED) == "bed"
    by_chrom, name = tracks.read_tracks(BEDGRAPH)
    assert name == "cov" and by_chrom["mt"] == [[0, 100, 5.0], [100, 200, -2.5]]
    by_chrom, _ = tracks.read_tracks(WIG, "w")
    assert by_chrom["mt"] == [[0, 10, 3.0], [50, 60, 4.0]] and by_chrom["other"] == [[0, 1, 1.0], [5, 6, 2.0]]
    fitted = tracks.fit([[0, 10, 1.0], [5000, 6000, 9.0]], 150)  # clipped to the sequence
    assert fitted == {"intervals": [[0, 10, 1.0]], "min": 1.0, "max": 1.0}
    many = tracks.fit([[i, i + 1, float(i % 2)] for i in range(300_000)], 300_000)  # averaged into bins
    assert len(many["intervals"]) <= tracks.MAX_INTERVALS and abs(many["intervals"][0][2] - 0.5) < 1e-9


def test_tracks_go_beside_a_sequence(lib):
    (seq,), _ = _import(lib, "mt.fa", ">mt\n" + "ACGT" * 60 + "\n")
    items, errors = _import(lib, "cov.bedgraph", BEDGRAPH)  # alone: refused with a hint
    assert not items and "data track" in errors[0]
    item = library.get_item(seq["id"])
    assert library.add_tracks(BEDGRAPH, item, "cov.bedgraph") == ["cov"]
    assert library.add_tracks(WIG, library.get_item(seq["id"]), "x.wig") == ["x"]  # the chrom naming it
    item = library.get_item(seq["id"])
    assert [t["name"] for t in library.data_tracks(item)] == ["cov", "x"]
    copy = library.get_item(library.duplicate(item))  # duplicated with it
    assert [t["name"] for t in library.data_tracks(copy)] == ["cov", "x"]
    library.remove_track(item, "cov")
    assert [t["name"] for t in library.data_tracks(library.get_item(seq["id"]))] == ["x"]
    library.remove_track(library.get_item(seq["id"]), "x")
    assert not library.get_item(seq["id"]).get("tracks")

def _vcf(chrom, rows, contig=None):
    head = "##fileformat=VCFv4.2\n" + (f"##contig=<ID={chrom},length={contig}>\n" if contig else "")
    head += "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2\n"
    return head + "".join(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT\t0\t1\n" for pos, ref, alt in rows)


def test_a_vcf_is_an_item_linked_to_its_reference(lib):
    seq = "ACGTACGGTTCA" * 30
    (mt,), _ = _import(lib, "mt.fa", ">mt\n" + seq + "\n")
    text = _vcf("mt", [(2, "C", "T"), (7, "G", "A"), (10, "TCA", "T")], contig=len(seq))
    assert sniff(text) == "vcf"
    (item,), errors = _import(lib, "calls.vcf", text)
    assert not errors and item["kind"] == "variants" and item["source"] == mt["id"] and item["sites"] == 3
    data = library.variants_data(library.get_item(item["id"]))
    assert data["reference"] == mt["id"] and data["vcf"].count("\nmt\t") == 3
    # wrong alleles, or a different length: kept, but not linked
    (bad,), _ = _import(lib, "bad.vcf", _vcf("mt", [(1, "G", "T"), (2, "A", "T")]))
    assert not bad.get("source") and "REF alleles differ" in bad["link_note"]
    (other,), _ = _import(lib, "len.vcf", _vcf("mt", [(2, "C", "T")], contig=99))
    assert not other.get("source") and "bp" in other["link_note"]
    # exported as it is
    from cladia.sequence_export import _export_text
    assert _export_text([library.get_item(item["id"])], "vcf").startswith("##fileformat=VCF")

def _bam_header(refs, sort="coordinate"):
    import gzip as gz
    text = f"@HD\tVN:1.6\tSO:{sort}\n".encode()
    body = b"BAM\x01" + len(text).to_bytes(4, "little") + text + len(refs).to_bytes(4, "little")
    for name, length in refs:
        raw = name.encode() + b"\0"
        body += len(raw).to_bytes(4, "little") + raw + length.to_bytes(4, "little")
    return gz.compress(body)


def test_bam_headers_are_matched_to_library_sequences(lib, monkeypatch):
    from cladia import bam_import, jobs
    (mt,), _ = _import(lib, "mt.fa", ">mt\n" + "ACGT" * 100 + "\n")
    bam = lib / "x.bam"
    bam.write_bytes(_bam_header([("mt", 400), ("pt", 900), ("mt2", 5)]))
    assert bam_import.kind_of(bam) == "bam"
    refs, sorted_ = bam_import.references(bam, "bam")
    assert refs == [("mt", 400), ("pt", 900), ("mt2", 5)] and sorted_
    pairs, unmatched = bam_import.match(refs)
    assert [(it["id"], name) for it, name in pairs] == [(mt["id"], "mt")] and len(unmatched) == 2
    sam = lib / "y.sam"
    sam.write_text("@HD\tVN:1.6\tSO:unsorted\n@SQ\tSN:mt\tLN:401\nr1\t0\tmt\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n")
    assert bam_import.kind_of(sam) == "sam"
    with pytest.raises(ValueError, match="401"):  # the right name, the wrong length: nothing to read onto
        bam_import.submit(sam)
    submitted = {}
    monkeypatch.setattr(jobs, "submit", lambda kind, title, func, **params: submitted.update(params) or {"id": "j1"})
    assert bam_import.submit(bam) == ("j1", ["mt"]) and submitted["pairs"] == [(mt["id"], "mt")]
    (lib / "z.cram").write_bytes(b"CRAM\x03\x00")
    with pytest.raises(ValueError, match="CRAM"):
        bam_import.submit(lib / "z.cram")

def _fastq(path, n, length=100, seed=1, gz=True):
    import gzip as gz_, random
    rnd = random.Random(seed)
    text = "".join(f"@r{i} x\n{''.join(rnd.choice('ACGT') for _ in range(length))}\n+\n{'I' * (length - 10)}{'5' * 10}\n" for i in range(n))
    if gz:
        path.write_bytes(gz_.compress(text.encode()))
    else:
        path.write_text(text)
    return path


def test_reads_become_an_item_with_their_report(lib):
    from cladia import reads, projects
    r1, r2 = _fastq(lib / "S1_R1.fastq.gz", 300), _fastq(lib / "S1_R2.fastq.gz", 300, seed=2)
    single = _fastq(lib / "other.fq", 50, length=80, gz=False)
    assert reads.is_fastq(r1) and reads.is_fastq(single) and not reads.is_fastq(lib / "mt.fa" if (lib / "mt.fa").exists() else r1.parent / "nothing")
    groups = reads.samples([r1, r2, single])
    assert [g["name"] for g in groups] == ["S1", "other"] and len(groups[0]["files"]) == 2
    stats = reads.file_stats(r1)
    assert stats["reads"] == 300 and stats["bases"] == 30000 and stats["lengths"] == [[100, 300]]
    assert stats["quality"][0] == 40 and stats["quality"][-1] == 20  # 'I' = 40, '5' = 20
    assert abs(sum(stats["composition"][b][0] for b in "ACGT") - 300) == 0 and len(stats["first"]) == 300
    job = {"log": "", "outputs": [], "id": "j"}
    reads.import_job(job, lib, "S1", [str(r1), str(r2)])
    item = library.get_item(job["outputs"][0]["id"])
    assert item["kind"] == "reads" and len(item["read_files"]) == 2
    data = library.reads_data(item)
    assert data["paired"] and not data["long"] and len(data["present"]) == 2 and len(data["files"][0]["sha256"]) == 64
    # project ZIPs leave the FASTQ files out unless asked
    out = lib / "p.zip"
    projects.export_zip(out)
    import zipfile
    names = zipfile.ZipFile(out).namelist()
    assert not any(n.endswith(".fastq.gz") for n in names) and any(n.endswith(".reads.json") for n in names)
    projects.export_zip(out, include_reads=True)
    assert sum(n.endswith(".fastq.gz") for n in zipfile.ZipFile(out).namelist()) == 2
    # the files can go, the report stays
    assert library.drop_read_files(item) == 2
    after = library.get_item(item["id"])
    assert after["reads_removed"] and not library.reads_data(after)["present"]
    with pytest.raises(ValueError):
        library.duplicate(after)

def test_tools_take_reads_items_or_files_on_disk(lib):
    from cladia import reads
    r1, r2 = _fastq(lib / "A_R1.fastq.gz", 20), _fastq(lib / "A_R2.fastq.gz", 20, seed=2)
    job = {"log": "", "outputs": [], "id": "j"}
    reads.import_job(job, lib, "A", [str(r1), str(r2)])
    item = library.get_item(job["outputs"][0]["id"])
    disk = _fastq(lib / "B.fastq", 10, gz=False)
    groups = reads.read_groups([f"item:{item['id']}", str(disk)])
    assert [g["name"] for g in groups] == ["A", "B"] and len(groups[0]["files"]) == 2
    assert all(Path(f).parent == library.library_dir() for f in groups[0]["files"])
    long = reads.read_groups([f"item:{item['id']}"], "ont")  # long reads: a sample per file
    assert [len(g["files"]) for g in long] == [1, 1]
    library.drop_read_files(library.get_item(item["id"]))
    with pytest.raises(ValueError, match="deleted"):
        reads.read_groups([f"item:{item['id']}"])
    with pytest.raises(ValueError, match="Not found"):
        reads.read_groups([str(lib / "nope.fastq")])