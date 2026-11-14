import cladia.execution as owner_execution
import cladia.tool_plugins.reference_annotation as owner_reference_annotation
"""GFF3 export, annotating from a reference (placement logic), saving a rerooted tree."""

from Bio import Phylo
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord
import shutil
from io import StringIO

from cladia import gff, library, transfer


def _record():
    r = SeqRecord(Seq("ATG" + "AAA" * 20 + "TAA" + "C" * 40), id="seq1", name="seq1",
                  annotations={"molecule_type": "DNA", "topology": "circular"})
    r.features = [
        SeqFeature(SimpleLocation(0, 106, strand=1), type="source", qualifiers={"organism": ["Chondrus crispus"]}),
        SeqFeature(SimpleLocation(0, 66, strand=1), type="gene", qualifiers={"gene": ["orf1"]}),
        SeqFeature(CompoundLocation([SimpleLocation(40, 66, strand=-1), SimpleLocation(0, 10, strand=-1)]),
                   type="CDS", qualifiers={"gene": ["orf2"], "note": ["a; b=c"], "translation": ["MK"]}),
    ]
    return r


# ── GFF3 ─────────────────────────────────────────────────────────────────

def test_gff3_lines():
    text = gff.records_to_gff3([_record()])
    lines = text.splitlines()
    assert lines[0] == "##gff-version 3"
    assert lines[1] == "##sequence-region seq1 1 106"
    cols = [l.split("\t") for l in lines[2:]]
    assert cols[0][2] == "region" and "Is_circular=true" in cols[0][8] and "organism=Chondrus crispus" in cols[0][8]
    assert cols[1][2:8] == ["gene", "1", "66", ".", "+", "."]
    # a two-part minus-strand CDS: one line per part, biological order; 26 bases
    # leave 2 of a codon, so the second part starts 1 base into its first codon
    assert [c[3:8] for c in cols[2:]] == [["41", "66", ".", "-", "0"], ["1", "10", ".", "-", "1"]]
    assert "note=a%3B b%3Dc" in cols[2][8] and "translation" not in cols[2][8]
    assert "##FASTA" not in text


def test_gff3_with_fasta():
    text = gff.records_to_gff3([_record()], with_fasta=True)
    assert text.split("##FASTA\n")[1].startswith(">seq1\nATGAAA")


# ── Annotating from a reference ──────────────────────────────────────────

def _hit(qs, qe, ss, se, pident=100.0, bits=100.0):
    return {"qs": qs, "qe": qe, "lo": min(ss, se) - 1, "hi": max(ss, se) - 1,
            "strand": 1 if ss <= se else -1, "matches": round(pident * (qe - qs + 1) / 100), "bits": bits}


def test_places_extend_and_chain():
    # two pieces of one query around an insertion in the target, ends unaligned
    hits = [_hit(5, 50, 105, 150, bits=90), _hit(51, 95, 160, 204, bits=80)]
    (p,) = transfer.places(hits, 100)
    assert (p["start"], p["end"], p["strand"]) == (100, 208, 1)
    # minus strand: the query's start is at the high end
    (p,) = transfer.places([_hit(1, 90, 300, 211)], 100)
    assert (p["start"], p["end"], p["strand"]) == (200, 299, -1)


def test_transfer_two_copies_limit_and_origin():
    ref = _record()
    lengths = {"f1p0": 66, "f2p0": 26, "f2p1": 10}
    hits = {
        # gene: found twice (an inverted repeat), and once too different
        "f1p0": [_hit(1, 66, 11, 76), _hit(1, 66, 576, 511, bits=99), _hit(1, 40, 300, 339, pident=80, bits=20)],
        # the CDS's parts: across the origin of a 600 bp circular target (doubled)
        "f2p0": [_hit(1, 26, 581, 606)],
        "f2p1": [_hit(1, 10, 641, 650)],
    }
    features, report = transfer.transfer(ref, "N" * 600, True, hits, lengths, limit=0.2)
    genes = [f for f in features if f["type"] == "gene"]
    assert [(g["start"], g["end"], g["strand"]) for g in genes] == [(10, 75, 1), (510, 575, -1)]
    assert "100.0 % identical" in genes[0]["notes"]["note"][0]
    (cds,) = [f for f in features if f["type"] == "CDS"]
    assert cds["strand"] == -1 and cds["locations"] == [{"start": 580, "end": 5}, {"start": 40, "end": 49}]
    assert cds["notes"]["translation"] == [""]  # recomputed for the target when saved
    assert report[0].startswith("2 of 2 features found, 3 placed")
    # stricter: the gene copies pass, a 30 %-different one wouldn't
    features, report = transfer.transfer(ref, "N" * 600, True, {"f1p0": [_hit(1, 66, 11, 76, pident=70)]}, lengths, 0.2)
    assert not features and "orf1 (30 % different)" in "\n".join(report) and "orf2 (not found)" in "\n".join(report)


GENE = "ATG" + "AAA" * 20 + "TAA"  # 66 bp: start, 20 codons, stop


def test_snap_moves_ends_to_codons():
    target = "CCCGGG" + GENE + "GGGCCC"  # the gene at 6..71
    # placed one codon late and two codons short: both ends move out
    parts, note = transfer.snap([(9, 65)], 1, target, False, 11)
    assert parts == [(6, 71)] and note == "; start moved 3 bp upstream, end moved 6 bp downstream (start/stop codons)"
    # already right: unchanged
    assert transfer.snap([(6, 71)], 1, target, False, 11) == ([(6, 71)], "")
    # the minus strand, the same gene turned round
    rc = str(Seq(target).reverse_complement())
    assert transfer.snap([(12, 68)], -1, rc, False, 11)[0] == [(6, 71)]


def test_snap_limits():
    # upstream of the placed start there's a stop before the ATG: it isn't used,
    # the next ATG downstream is
    target = "ATG" + "TAA" + "AAA" * 3 + "ATG" + "AAA" * 20 + "TAA"  # ATGs at 0 and 15
    parts, _ = transfer.snap([(9, len(target) - 1)], 1, target, False, 11)
    assert parts[0][0] == 15
    # a stop far inside (a frameshift?) is left for the viewer to show; the end stays
    target = "ATG" + "AAA" * 5 + "TAA" + "AAA" * 60 + "CCC"
    assert transfer.snap([(0, len(target) - 1)], 1, target, False, 11)[0] == [(0, len(target) - 1)]
    # across the origin of a circular target (positions beyond its end wrap)
    target = GENE[30:] + "CCCCCC" + GENE[:30]
    n = len(target)
    parts, _ = transfer.snap([(n - 27, n + 30)], 1, target, True, 11)
    assert parts == [(n - 30, n + 35)]


def test_transfer_skips_existing():
    lengths = {"f1p0": 66, "f2p0": 26, "f2p1": 10}
    hits = {"f1p0": [_hit(1, 66, 11, 76)]}
    features, report = transfer.transfer(_record(), "N" * 600, False, hits, lengths, 0.2, existing=[("gene", 1, [(10, 75)])])
    assert features == [] and "1 not copied" in report[0]
    # mostly the same place is the same feature; little overlap, or the other strand, isn't
    assert transfer.same_feature(("gene", 1, [(10, 75)]), ("gene", 1, [(16, 80)]))
    assert not transfer.same_feature(("gene", 1, [(10, 75)]), ("gene", 1, [(60, 140)]))
    assert not transfer.same_feature(("gene", 1, [(10, 75)]), ("gene", -1, [(10, 75)]))
    assert not transfer.same_feature(("gene", 1, [(10, 75)]), ("CDS", 1, [(10, 75)]))


def test_reference_job(tmp_path, monkeypatch):
    """The job with BLAST replaced: features land on the new item, translated."""
    from cladia import config, jobs
    from cladia.genbank import write_record
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    library.ensure_folders()
    ref = SeqRecord(Seq("ATGAAACCCGGGTAA" + "T" * 30), id="ref", name="ref", annotations={"molecule_type": "DNA"})
    ref.features = [SeqFeature(SimpleLocation(0, 15, strand=1), type="CDS",
                               qualifiers={"gene": ["abc"], "transl_table": ["11"], "translation": ["MKPG"]})]
    target = SeqRecord(Seq("GG" + "ATGAAACCAGGGTAA" + "A" * 20), id="tgt", name="tgt", annotations={"molecule_type": "DNA"})
    write_record(ref, tmp_path / "ref.gb")
    write_record(target, tmp_path / "tgt.gb")
    (rid, tid), _ = library.import_paths([tmp_path / "ref.gb", tmp_path / "tgt.gb"])
    monkeypatch.setattr(owner_execution, "ensure_tools_image", lambda job: None)
    def fake_blast(job, image, args, workdir, entrypoint=None):  # each task's hits, as BLAST would write them
        for task in (workdir / "tasks").glob("*.sh"):
            (workdir / f"hits{task.stem}.tsv").write_text("f0p0\ttarget\t93.33\t15\t1\t15\t3\t17\t25\n")
        return ""

    def run(**kwargs):
        workdir = tmp_path / "w"
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir()
        job = {"id": "t", "log": "", "outputs": [], "cores": 1}
        owner_reference_annotation.run_annotation(job, workdir, [library.get_item(tid)], library.get_item(rid), 0.2, **kwargs)
        return job

    monkeypatch.setattr(owner_execution, "tool", fake_blast)
    job = run()
    (out,) = job["outputs"]
    (cds,) = [f for f in library.records(library.get_item(out["id"]))[0].features if f.type == "CDS"]
    assert (int(cds.location.start), int(cds.location.end)) == (2, 17)
    assert cds.qualifiers["translation"] == ["MKPG"] and "93.3 % identical" in cds.qualifiers["note"][0]
    # the target's own annotations: kept, or left out with replace
    annotated = library.records(library.get_item(out["id"]))[0]
    annotated.features.append(SeqFeature(SimpleLocation(25, 30, strand=1), type="misc_feature", qualifiers={"label": ["own"]}))
    library.save_record(library.get_item(tid), annotated)
    for replace, own in ((False, True), (True, False)):
        job = run(replace=replace)
        labels = [q for f in library.records(library.get_item(job["outputs"][0]["id"]))[0].features for q in f.qualifiers.get("label", [])]
        assert ("own" in labels) == own


# ── Trees ────────────────────────────────────────────────────────────────

def test_reroot_keeps_supports_on_their_branches(tmp_path):
    path = tmp_path / "t.nwk"
    path.write_text("((A:0.1,B:0.2)91:0.05,(C:0.1,(D:0.1,E:0.1)77:0.03)85:0.04,F:0.3);\n")
    library.reroot_tree({"path": str(path)}, ["A", "B"])
    tree = Phylo.read(StringIO(path.read_text()), "newick")
    root = tree.root
    assert len(root.clades) == 2 and [c.branch_length for c in root.clades] == [0.025, 0.025]
    supports = {frozenset(t.name for t in c.get_terminals()): c.confidence
                for c in tree.get_nonterminals() if c.confidence is not None}
    # {D,E} | rest keeps 77, {C,D,E} | rest keeps 85, {A,B} | rest shows its 91 once
    assert supports == {frozenset("DE"): 77, frozenset("CDE"): 85, frozenset("CDEF"): 91}


def test_tree_nodes_for_the_viewer(tmp_path):
    path = tmp_path / "t.nwk"
    path.write_text("((A:0.1,B:0.2)91:0.05,(C:0.1,(D:0.1,E:0.1)80/95:0.03)85.5:0.04,F:0.3);\n")
    nodes = library.tree_nodes({"path": str(path)})
    assert [n["name"] for n in nodes] == [None, None, "A", "B", None, "C", None, "D", "E", "F"]
    assert [n["parent"] for n in nodes] == [-1, 0, 1, 1, 0, 4, 4, 6, 6, 0]  # parents before children, in order
    assert [n["support"] for n in nodes if n["name"] is None] == [None, "91", "85.5", "80/95"]
    assert nodes[3]["length"] == 0.2


def test_tree_nodes_deep_tree(tmp_path):
    """A ladder of 3000 tips: no recursion limit."""
    newick = "t0"
    for i in range(1, 3000):
        newick = f"({newick}:0.1,t{i}:0.1)"
    (tmp_path / "deep.nwk").write_text(newick + ";\n")
    nodes = library.tree_nodes({"path": str(tmp_path / "deep.nwk")})
    assert sum(1 for n in nodes if n["name"]) == 3000


# ── Importing GFF3 annotations ───────────────────────────────────────────

GFF = """##gff-version 3
##sequence-region chr1 1 120
chr1\tsrc\tregion\t1\t120\t.\t+\t.\tID=chr1;Is_circular=true
chr1\tsrc\tgene\t10\t60\t.\t+\t.\tID=g1;Name=abcA
chr1\tsrc\tmRNA\t10\t60\t.\t+\t.\tID=m1;Parent=g1
chr1\tsrc\tCDS\t10\t30\t.\t+\t0\tID=c1;Parent=m1;product=ABC%3B transporter
chr1\tsrc\tCDS\t41\t60\t.\t+\t0\tID=c1;Parent=m1;product=ABC%3B transporter
chr1\tsrc\ttRNA\t70\t95\t.\t-\t.\tID=t1;Name=trnL;Note=a note
chr1\tsrc\tfive_prime_UTR\t110\t125\t.\t+\t.\tID=u1;Name=utr
"""


def test_read_gff3():
    features, fasta = gff.read_gff3(GFF)
    assert list(features) == ["chr1"] and not fasta
    by_type = {f["type"]: f for f in features["chr1"]}
    assert set(by_type) == {"gene", "mRNA", "CDS", "tRNA", "5'UTR"}  # the region line is the sequence itself
    cds = by_type["CDS"]
    assert cds["parts"] == [(9, 29), (40, 59)] and cds["name"] == "abcA"  # named after its gene, through the mRNA
    assert cds["qualifiers"]["gene"] == ["abcA"] and cds["qualifiers"]["product"] == ["ABC; transporter"]
    trna = by_type["tRNA"]
    assert trna["strand"] == -1 and trna["qualifiers"] == {"gene": ["trnL"], "note": ["a note"]}
    # editor features: the UTR past the end of a circular sequence crosses the origin
    ed = gff.editor_features(features["chr1"], 120, True)
    utr = next(f for f in ed if f["type"] == "5'UTR")
    assert (utr["start"], utr["end"]) == (109, 4)
    import pytest
    with pytest.raises(ValueError, match="outside the sequence"):
        gff.editor_features(features["chr1"], 120, False)


def test_gff3_phase_becomes_codon_start():
    text = "##gff-version 3\ns\t.\tCDS\t5\t40\t.\t-\t2\tID=x;Name=orf\n"
    (f,) = gff.read_gff3(text)[0]["s"]
    assert f["qualifiers"]["codon_start"] == ["3"]


def _library(tmp_path, monkeypatch):
    from cladia import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()


def test_import_annotations(tmp_path, monkeypatch):
    from cladia.genbank import write_record
    _library(tmp_path, monkeypatch)
    rec = SeqRecord(Seq("ACGT" * 30), id="NC_000001.1", name="NC_000001",
                    annotations={"molecule_type": "DNA", "topology": "circular", "accessions": ["NC_000001"]})
    write_record(rec, tmp_path / "mine.gb")
    (sid,), _ = library.import_paths([tmp_path / "mine.gb"])
    # onto a chosen sequence: the file's only seqid, whatever it is called
    results, errors = library.import_annotations(GFF, library.get_item(sid))
    assert not errors and results[0]["added"] == 5
    feats = library.records(library.get_item(sid))[0].features
    assert sorted(f.type for f in feats) == ["5'UTR", "CDS", "gene", "mRNA", "tRNA"]
    # again: nothing new
    results, _ = library.import_annotations(GFF, library.get_item(sid))
    assert (results[0]["added"], results[0]["skipped"]) == (0, 5)
    # by seqid: "NC_000001.1" names it (accession), "other" names nothing
    text = GFF.replace("chr1", "NC_000001.1").replace("trnL", "trnX").replace("\t70\t95", "\t71\t96") + \
        "other\t.\tgene\t1\t10\t.\t+\t.\tID=z\n"
    results, errors = library.import_annotations(text)
    assert [r["added"] for r in results] == [1] and errors == ["other: no sequence in Cladia has this name"]


def test_gff3_with_fasta_imports_sequences(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    (tmp_path / "ann.gff3").write_text(GFF.replace("\t125\t", "\t118\t") + "##FASTA\n>chr1\n" + "ACGT" * 30 + "\n")
    (new,), errors = library.import_paths([tmp_path / "ann.gff3"])
    item = library.get_item(new)
    assert not errors and item["kind"] == "sequence" and item["name"] == "chr1"
    assert len(library.records(item)[0].features) == 5
    (tmp_path / "bare.gff3").write_text(GFF)
    _, errors = library.import_paths([tmp_path / "bare.gff3"])
    assert "drop the file on a sequence" in errors[0]


def test_gene_follows_its_snapped_cds():
    # reference: a gene with 3 bases more on each side than its CDS
    ref = SeqRecord(Seq("CCCGGG" + GENE + "GGGCCC"), id="ref", name="ref", annotations={"molecule_type": "DNA"})
    ref.features = [SeqFeature(SimpleLocation(3, 75, strand=1), type="gene", qualifiers={"gene": ["orf1"]}),
                    SeqFeature(SimpleLocation(6, 72, strand=1), type="CDS", qualifiers={"gene": ["orf1"]})]
    # the target's stop is two codons later: the copied CDS's end moves 6 bp downstream
    target = "CCCGGG" + "ATG" + "AAA" * 20 + "TAC" + "AAA" + "TAA" + "GGGCCC"
    lengths = {"f0p0": 72, "f1p0": 66}
    n = len(target)
    for flip in (False, True):  # the same, with the target turned round
        seq = str(Seq(target).reverse_complement()) if flip else target
        hit = (lambda qs, qe, s, e: _hit(qs, qe, n + 1 - s, n + 1 - e)) if flip else _hit
        hits = {"f0p0": [hit(1, 72, 4, 75)], "f1p0": [hit(1, 66, 7, 72)]}
        features, report = transfer.transfer(ref, seq, False, hits, lengths, 0.3, code=11)
        gene, cds = sorted(features, key=lambda f: f["type"] != "gene")
        where = lambda f: (n - 1 - f["end"], n - 1 - f["start"]) if flip else (f["start"], f["end"])
        assert where(cds) == (6, 77)
        assert where(gene) == (3, 80), flip  # still 3 bases beyond the CDS at each end
        assert "ends fitted to its CDS" in gene["notes"]["note"][-1] and "1 genes fitted" in report[0]
