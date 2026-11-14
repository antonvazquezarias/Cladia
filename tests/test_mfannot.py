"""MFannot engine: its feature table read into features, and a run with the program faked."""
import pytest
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqFeature import FeatureLocation, SeqFeature

import cladia.engine_api as api
from cladia import linux
from cladia.tool_plugins import mfannot
from cladia.tool_registry import build_registry

# mf2sqn's five-column table: a cox1 on the minus strand in two exons with an
# intron, an rRNA, a tRNA, and a ycf ORF written without a feature type
TABLE = """>Feature C_0
900\t101\tgene
\t\t\tgene\tcox1
900\t601\tCDS
500\t101
\t\t\tproduct\tcytochrome c oxidase subunit 1
\t\t\tprotein_id\tlcl| C-cox1
\t\t\ttransl_except\t(pos:300..302, aa : W)
600\t501\tintron
\t\t\tnumber\t1
1001\t1500\tgene
\t\t\tgene\trnl
1001\t1500\trRNA
\t\t\tproduct\t23S ribosomal RNA
1601\t1672\tgene
\t\t\tgene\ttrnW(cca)
1601\t1672\ttRNA
\t\t\tproduct\ttRNA-Trp
1701\t1900\tgene
\t\t\tgene\torf66
1701\t1900
\t\t\tproduct\tx
\t\t\tprotein_id\tlcl| G-ycf3
"""


def test_table_becomes_genes_with_parts():
    genes = mfannot.read_table(TABLE, code=4)
    assert [g["name"] for g in genes] == ["cox1", "rnl", "trnW(cca)", "orf66"]
    cox1 = genes[0]
    assert cox1["strand"] == -1 and cox1["parts"] == [(101, 900)]
    cds = cox1["children"][0]
    assert cds["type"] == "CDS" and cds["parts"] == [(601, 900), (101, 500)]
    assert cds["attrs"]["transl_table"] == "4" and cds["attrs"]["transl_except"] == "(pos:300..302,aa:Trp)"
    assert cds["attrs"]["protein_id"] == "C-cox1"
    assert cds["children"][0]["type"] == "intron"  # (inside the CDS it interrupts)
    orf = genes[3]["children"][0]
    assert orf["type"] == "CDS" and orf["attrs"]["product"] == "hypothetical protein"
    assert "gene" not in genes[3]["attrs"]  # an ORF's made-up name isn't a /gene


def test_features_carry_their_gene_and_run_five_to_three():
    features = mfannot.seq_features(mfannot.read_table(TABLE, code=4))
    cds = next(f for f in features if f.type == "CDS" and f.qualifiers.get("gene") == ["cox1"])
    assert [(int(p.start), int(p.end)) for p in cds.location.parts] == [(600, 900), (100, 500)]
    assert cds.location.strand == -1
    orf = [f for f in features if f.qualifiers.get("label") == ["orf66"]]
    assert {f.type for f in orf} == {"gene", "CDS"}
    assert [f.type for f in features][:3] == ["gene", "CDS", "intron"]


def test_engine_declares_its_image():
    registry = build_registry()
    assert "mfannot" in [e["id"] for e in registry.enginesFor("annotate.denovo")]
    d = linux.DISTROS["mfannot"]
    assert d["image"] == mfannot.IMAGE and "@sha256:" in d["image"]
    assert linux.kind_of(mfannot.IMAGE) == "mfannot"


@pytest.mark.parametrize("replace", [True, False])
def test_run_with_mfannot_faked(tmp_path, monkeypatch, replace):
    record = SeqRecord(Seq("ACGT" * 500), id="mt", name="mt", annotations={"molecule_type": "DNA", "topology": "circular"})
    record.features = [SeqFeature(FeatureLocation(0, 2000, 1), type="source"),
                       SeqFeature(FeatureLocation(1000, 1500, 1), type="rRNA", qualifiers={"gene": ["rnl"]}),
                       SeqFeature(FeatureLocation(1950, 1990, 1), type="misc_feature")]
    monkeypatch.setattr(api, "records", lambda item: [record])
    monkeypatch.setattr(api, "steps", lambda job, *names: job.update(progress={"at": 0}))
    monkeypatch.setattr(api, "watch_log", lambda *a, **k: None)
    calls = []

    def fake(job, image, args, workdir, entrypoint=None):
        calls.append((image, args, entrypoint))
        (workdir / "in" / "s00000.fasta.new.tbl").write_text(TABLE)
        (workdir / "run.log").write_text("[1/1] s00000\n")
    monkeypatch.setattr(api, "run_container", fake)
    done = {}
    monkeypatch.setattr(api, "annotated", lambda job, workdir, results, suffix, into, apply, busy:
                        done.update(results=results, suffix=suffix))
    job = {"log": ""}
    mfannot.run(job, tmp_path, "annotate.denovo", [{"label": "Mt"}], "", organelle="mitochondrion", replace=replace)
    image, args, entrypoint = calls[0]
    assert image == mfannot.IMAGE and entrypoint == "bash" and "mfannot -g 4 --tbl s00000.fasta" in args[-1]
    assert done["suffix"] == "_mfannot" and "across the origin" in job["log"]
    saved = api.read_records(done["results"][0][1], "genbank")[0]
    types = [f.type for f in saved.features]
    if replace:
        assert "misc_feature" not in types and types.count("rRNA") == 1
    else:  # own ones kept; MFannot's rRNA is the same as the record's own: not added twice
        assert "misc_feature" in types and types.count("rRNA") == 1
        assert any(f.qualifiers.get("note") == ["added by MFannot"] for f in saved.features if f.type == "tRNA")


def test_unknown_code_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "records", lambda item: [SeqRecord(Seq("ACGT"), id="a")])
    with pytest.raises(ValueError, match="genetic code"):
        mfannot.run({"log": ""}, tmp_path, "annotate.denovo", [{"label": "a"}], "", organelle="7")
