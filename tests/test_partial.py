"""Partial ends (<1..>500), qualifiers GenBank doesn't know, and the checks that go with them."""
from io import StringIO

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import AfterPosition, BeforePosition, FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from cladia import checks, submission
from cladia.genbank import editor_to_record, record_to_editor, read_records

GB = """LOCUS       P1                        90 bp    DNA     linear   UNA 01-NOV-2026
DEFINITION  test.
ACCESSION   P1
FEATURES             Location/Qualifiers
     source          1..90
                     /organism="Test organism"
     CDS             <1..>60
                     /product="cut short"
                     /ID="g1"
                     /Dbxref="GeneID:1"
     CDS             complement(<10..80)
                     /product="other way"
ORIGIN
        1 atgaaataaa tgaaataaat gaaataaatg aaataaatga aataaatgaa ataaatgaaa
       61 taaatgaaat aaatgaaata aatgaaataa
//
"""


def record():
    return read_records(StringIO(GB), "genbank")[0]


def test_partial_ends_are_kept_through_the_editor():
    data = record_to_editor(record())
    one, two = data["features"]
    assert one["partial"] == {"left": True, "right": True} and (one["start"], one["end"]) == (0, 59)
    assert two["partial"] == {"left": True, "right": False} and two["strand"] == -1
    back = editor_to_record(data, original=record())
    assert isinstance(back.features[1].location.start, BeforePosition) and isinstance(back.features[1].location.end, AfterPosition)
    out = StringIO()
    SeqIO.write(back, out, "genbank")
    assert "<1..>60" in out.getvalue() and "complement(<10..80)" in out.getvalue()
    # a feature without markers stays without
    data["features"][0].pop("partial")
    plain = editor_to_record(data, original=record())
    assert not isinstance(plain.features[1].location.start, BeforePosition)


def test_the_table_writes_partial_ends_and_leaves_out_unknown_qualifiers():
    from collections import Counter
    dropped = Counter()
    lines = submission.table(record(), dropped=dropped).splitlines()
    assert "<1\t>60\tCDS" in lines                       # 5' partial, 3' partial
    assert "80\t>10\tCDS" in lines                        # minus strand: the left end is the 3' one
    assert not any("/ID" in x or "\tID\t" in x or "Dbxref" in x for x in lines)
    assert dropped == {"ID": 1, "Dbxref": 1}
    assert "\t\t\tproduct\tcut short" in lines


def test_checks_know_partial_and_unknown_qualifiers():
    data = record_to_editor(record())
    data["organism"] = "Test"
    codes = lambda: [(p["feature"], p["code"]) for p in checks.check(data)]
    found = codes()
    assert (0, "qualifier") in found                       # /ID, /Dbxref
    assert not any(c in ("cds-start", "cds-stop", "cds-frame") and f == 0 for f, c in found)  # partial at both ends
    # the second CDS (minus strand) is partial at its 3' end only: a start codon is still wanted
    assert not any(c == "cds-stop" and f == 1 for f, c in found)
    data["features"][0].pop("partial")
    assert any(c in ("cds-start", "cds-stop", "cds-frame") for f, c in codes() if f == 0)
    data["features"].append(dict(data["features"][1], id="f9"))
    assert (2, "duplicate") in codes()
