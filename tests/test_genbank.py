"""Round-trip tests: file -> editor JSON -> file must not lose information.

Run with:  python -m pytest tests
"""

from pathlib import Path

from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SeqFeature, SimpleLocation

from cladia.genbank import cds_protein, editor_to_record, read_record, record_to_editor, write_record

DATA = Path(__file__).parent / "data"


def assert_same(a, b):
    assert str(a.seq) == str(b.seq)
    assert a.annotations.get("topology") == b.annotations.get("topology")
    fa = sorted(a.features, key=lambda f: (int(f.location.start), f.type, str(f.location)))
    fb = sorted(b.features, key=lambda f: (int(f.location.start), f.type, str(f.location)))
    assert len(fa) == len(fb)
    for x, y in zip(fa, fb):
        assert x.type == y.type
        assert str(x.location) == str(y.location)
        assert x.qualifiers == y.qualifiers


def roundtrip(record, tmp_path):
    data = record_to_editor(record)
    out = tmp_path / "out.gb"
    write_record(editor_to_record(data, original=record), out)
    return read_record(out)


def test_real_file_roundtrip(tmp_path):
    original = read_record(DATA / "synthetic_mt.gb")
    assert_same(original, roundtrip(original, tmp_path))


def test_split_and_origin_features(tmp_path):
    record = read_record(DATA / "synthetic_mt.gb")
    record.annotations["topology"] = "circular"
    n = len(record)
    record.features += [
        # intron-containing gene on the minus strand
        SeqFeature(CompoundLocation([SimpleLocation(900, 1000, -1), SimpleLocation(500, 600, -1)]),
                   type="CDS", qualifiers={"gene": ["splitminus"]}),
        # feature crossing the origin
        SeqFeature(CompoundLocation([SimpleLocation(n - 50, n, 1), SimpleLocation(0, 30, 1)]),
                   type="misc_feature", qualifiers={"label": ["origin"]}),
    ]
    assert_same(record, roundtrip(record, tmp_path))


def test_translation_follows_edits():
    """Only a CDS whose DNA changed gets a new /translation; others keep theirs."""
    record = read_record(DATA / "synthetic_mt.gb")
    data = record_to_editor(record)
    cdss = [f for f in data["features"] if f["type"] == "CDS"]
    edited, untouched = cdss[3], cdss[4]  # cox1 and nad2, both on the plus strand
    pos = edited["start"] + 30  # inside cox1: change one base
    seq = data["sequence"]
    data["sequence"] = seq[:pos] + ("A" if seq[pos] != "A" else "C") + seq[pos + 1:]
    result = editor_to_record(data, original=record)
    by_name = {f.qualifiers["label"][0]: f for f in result.features if f.type == "CDS"}
    new = by_name[edited["name"]]
    assert new.qualifiers["translation"][0] == cds_protein(new, result.seq, 1)
    assert new.qualifiers["translation"][0] != edited["notes"]["translation"][0]
    assert by_name[untouched["name"]].qualifiers["translation"] == untouched["notes"]["translation"]


def test_cds_protein_codes_and_starts():
    seq = Seq("GTGTGATGGAAATAA")  # GTG start, TGA (W in code 4, stop in code 1)
    cds = SeqFeature(SimpleLocation(0, 15, 1), type="CDS")
    assert cds_protein(cds, seq, 4) == "MWWK"  # alternative start written as M, no final stop
    assert cds_protein(cds, seq, 1) == "V*WK"  # internal stop: translated codon by codon
    cds.qualifiers["transl_table"] = ["4"]
    assert cds_protein(cds, seq, 1) == "MWWK"  # the CDS's own /transl_table wins


def test_rename_and_edit(tmp_path):
    record = read_record(DATA / "synthetic_mt.gb")
    data = record_to_editor(record)
    cob = next(f for f in data["features"] if f["type"] == "CDS" and f["name"] == "cob")
    cob["name"] = "cob_renamed"
    data["sequence"] = data["sequence"] + "ACGT"
    result = editor_to_record(data, original=record)
    assert len(result) == len(record) + 4
    names = [f.qualifiers.get("label", [""])[0] for f in result.features]
    assert "cob_renamed" in names

