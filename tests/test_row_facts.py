"""The list's row details: sequences carry circular/length in items(), other kinds
get a few numbers from /api/library/facts, cached until their file changes."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    monkeypatch.setattr(library, "_row_facts", {})
    from cladia.server import app
    return TestClient(app)


def upload(client, name, text):
    return client.post("/api/library/upload", json={"filename": name, "text": text}).json()["ids"]


GENBANK = """LOCUS       ring                      12 bp    DNA     circular UNK 01-JAN-1980
DEFINITION  .
ACCESSION   ring
FEATURES             Location/Qualifiers
ORIGIN
        1 acgtacgtac gt
//
"""


def test_row_facts_for_each_kind(client):
    ring = upload(client, "ring.gb", GENBANK)[0]
    assert library.get_item(ring)["circular"] is True
    upload(client, "pair.fasta", ">a\nACGTACGT\n>b\nACGTAC\n")  # a list of two
    aln = upload(client, "aln.fasta", ">a\nACGT-A\n>b\nACGTTA\n>c\nAC-TTA\n")[0]
    tree = upload(client, "t.nwk", "((A:1,B:1):1,(C:1,D:1):1,E:1);\n")[0]
    figure = client.post("/api/figures", json={"source": tree}).json()["id"]
    facts = client.get("/api/library/facts").json()
    lists = [i for i in library.items() if i["kind"] == "list"]
    assert facts[lists[0]["id"]] == {"total": 14, "unit": "bp"}
    assert facts[aln] == {"count": 3, "columns": 6}
    assert facts[tree] == {"tips": 5}
    assert facts[figure] == {"of": "tree"}
    assert ring not in facts  # sequences carry their own


def test_row_facts_follow_changes(client):
    tree = upload(client, "t.nwk", "(A:1,B:1,C:1);\n")[0]
    assert library.row_facts()[tree] == {"tips": 3}
    Path(library.get_item(tree)["path"]).write_text("(A:1,B:1,C:1,D:1,E:1);\n", encoding="utf-8")
    assert library.row_facts()[tree] == {"tips": 5}
    Path(library.get_item(tree)["path"]).write_text("((A:1,B:1", encoding="utf-8")
    assert tree not in library.row_facts()  # unreadable: no numbers, no error
