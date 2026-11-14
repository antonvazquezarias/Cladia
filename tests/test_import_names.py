"""Display names stay independent of collision-safe storage filenames."""
from io import StringIO
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from cladia import config, library


@pytest.fixture
def named_library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    library.ensure_folders()
    library.add_folder("Parent")
    library.add_folder("Child", "Parent")


def sequence_text(fmt, count):
    records = [SeqRecord(Seq("ACGTACGT"), id=f"sample{i}", name=f"sample{i}",
                         annotations={"molecule_type": "DNA"}) for i in range(count)]
    stream = StringIO()
    SeqIO.write(records, stream, fmt)
    return stream.getvalue()


@pytest.mark.parametrize("fmt,count", [(fmt, count) for fmt in ("fasta", "genbank", "embl") for count in (1, 2)])
def test_sequence_import_names(named_library, fmt, count):
    expected = "sample0" if count == 1 else "sample file"
    check_import_names(f"sample file.{fmt}", sequence_text(fmt, count), expected)


@pytest.mark.parametrize("filename,text,expected", [
    ("sample tree.nwk", "(a:0.1,b:0.2);", "sample tree"),
    ("sample alignment.fasta", ">a\nAC-GT\n>b\nACCGT\n", "sample alignment"),
    ("sample graph.gfa", "H\tVN:Z:1.0\nS\ta\tACGT\n", "sample graph"),
    ("sample.gff3", "##gff-version 3\nsample0\t.\tgene\t1\t4\t.\t+\t.\tID=g\n##FASTA\n>sample0\nACGT\n", "sample0"),
    ("sample.nex", "#NEXUS\nbegin data;\ndimensions ntax=2 nchar=4;\nformat datatype=dna;\nmatrix\na ACGT\nb ACGA\n;\nend;\n", "sample"),
])
def test_other_import_names(named_library, filename, text, expected):
    check_import_names(filename, text, expected)


def check_import_names(filename, text, expected):
    ids = []
    for folder in ("Parent", "Parent/Child", "Parent/Child"):
        added, errors = library.import_text(filename, text, folder)
        assert not errors
        ids.extend(added)
    items = [library.get_item(i) for i in ids]
    assert [i["name"] for i in items] == [expected] * 3
    assert [i["label"] for i in items] == [expected] * 3
    assert [i["folder"] for i in items] == ["Parent", "Parent/Child", "Parent/Child"]
    assert len({i["path"] for i in items}) == 3
    assert len({i["id"] for i in items}) == 3
    assert len({Path(i["path"]).read_bytes() for i in items}) == 1
    assert [i["name"] for i in library.load()] == [expected] * 3


def test_job_output_and_split_names(named_library, tmp_path):
    source = tmp_path / "output.gb"
    source.write_text(sequence_text("genbank", 2))
    ids = [library.add_output(source, "my result", "list") for _ in range(2)]
    assert [library.get_item(i)["name"] for i in ids] == ["my result"] * 2
    split_ids = [i for owner in ids for i in library.split(owner, remove_source=False)]
    assert [library.get_item(i)["name"] for i in split_ids] == ["sample0", "sample1"] * 2
    assert len({library.get_item(i)["path"] for i in split_ids}) == 4
