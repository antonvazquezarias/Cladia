import pytest

from cladia import submitter

PRESET = submitter.clean({
    "name": "Fucus project", "authors": "Doe, Jane\nRoe, Richard B.\nJean-Pierre Dupont",
    "email": "a@b.org", "phone": "+34 600", "institution": "University of Santiago", "department": 'Dept. of "Biology"',
    "street": "Rua 1", "city": "Santiago", "state": "A Coruna", "postal_code": "15782", "country": "Spain",
    "title": "Mitochondrial genomes of Fucus", "hold": "2027-03-04",
})


def test_template_round_trip():
    text = submitter.write_sbt(PRESET)
    assert text.startswith("Submit-block ::= {") and "Seqdesc ::= pub {" in text
    assert 'div "Dept. of ""Biology"""' in text            # quotes doubled, as ASN.1 wants
    assert "hup TRUE" in text and "year 2027" in text and text.rstrip().endswith("}")
    back = submitter.read_sbt(text)
    for key in ("email", "phone", "institution", "department", "street", "city", "state", "postal_code",
                "country", "title", "hold", "citation"):
        assert back[key] == PRESET[key], key
    # "First Last" is written, and read back, as "Last, First"; the middle name of John R. survives
    assert back["authors"].splitlines() == ["Doe, Jane", "Roe, Richard B.", "Dupont, Jean-Pierre"]
    assert back["contact"] == ""                          # the contact was the first author


def test_initials_and_contact():
    assert submitter.initials("Jean-Pierre R.") == "J.-P.R."
    text = submitter.write_sbt({**PRESET, "contact": "Lee, Sam"})
    assert submitter.read_sbt(text)["contact"] == "Lee, Sam"
    assert 'initials "J.-P."' in text


def test_no_date_and_no_title():
    text = submitter.write_sbt({**PRESET, "hold": "", "title": ""})
    assert "hup" not in text and "reldate" not in text and "title" not in text
    assert submitter.read_sbt(text)["hold"] == ""


def test_reads_a_template_with_comments_and_other_layout():
    text = '''-- made by the NCBI submission portal
Submit-block ::= {
  contact { contact { name name { last "Doe", first "Jane", initials "J." },
    affil std { affil "Somewhere", city "Town", country "Italy", email "j@d.it" } } },
  cit { authors { names std { { name name { last "Doe", first "Jane", initials "J." } } },
    affil std { affil "Somewhere", country "Italy" } } },
  subtype new
}
Seqdesc ::= pub { pub { gen { cit "in press", authors { names std { { name name { last "Doe", first "Jane" } } } },
  title "T" } } }
'''
    p = submitter.read_sbt(text)
    assert p["authors"] == "Doe, Jane" and p["institution"] == "Somewhere" and p["email"] == "j@d.it"
    assert p["citation"] == "in press" and p["title"] == "T" and p["name"] == "Doe"


def test_not_a_template():
    with pytest.raises(ValueError):
        submitter.read_sbt("LOCUS x 4 bp")
    with pytest.raises(ValueError):
        submitter.write_sbt({**PRESET, "authors": ""})


def test_presets_are_checked():
    assert submitter.check_presets([PRESET])[0]["name"] == "Fucus project"
    with pytest.raises(ValueError):
        submitter.check_presets([{**PRESET, "name": ""}])
    with pytest.raises(ValueError):
        submitter.check_presets([PRESET, {**PRESET, "name": "fucus PROJECT"}])
    with pytest.raises(ValueError):
        submitter.check_presets([{**PRESET, "hold": "next year"}])


def test_settings_import_and_export(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from cladia import config, genbank, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    assert genbank.sniff("-- c\nSubmit-block ::= {\n}") == "sbt"
    assert client.put("/api/settings", json={"submitters": [PRESET]}).json()["submitters"][0]["name"] == "Fucus project"
    assert client.put("/api/settings", json={"submitters": [{"name": ""}]}).status_code == 400
    # a template dropped on the library becomes a submitter, not an item
    text = submitter.write_sbt(PRESET)
    done = client.post("/api/library/upload", json={"filename": "other.sbt", "text": text}).json()
    assert done["ids"] == [] and done["errors"] == [] and "kept as the submitter" in done["notes"][0]
    names = [p["name"] for p in client.get("/api/settings").json()["submitters"]]
    assert names == ["Fucus project", "Doe"]
    # the dialog's endpoints
    assert client.post("/api/submitters/parse", json={"text": text}).json()["authors"].startswith("Doe, Jane")
    assert client.post("/api/submitters/parse", json={"text": "x"}).status_code == 400
    assert client.post("/api/submitters/sbt", json={"preset": PRESET}).json()["text"] == text


def test_submission_uses_the_submitter(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from cladia import config, library
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    client = TestClient(app)
    client.put("/api/settings", json={"submitters": [PRESET]})
    text = (Path(__file__).parent / "data" / "synthetic_mt.gb").read_text()
    item = client.post("/api/library/upload", json={"filename": "mt.gb", "text": text}).json()["ids"][0]
    out = client.post("/api/export/submission", json={"ids": [item], "submitter": "Fucus project",
                                                       "overrides": {"authors": "Changed, Author", "title": "New title"}}).json()
    assert 'last "Changed"' in out["sbt"] and "New title" in out["sbt"] and "Roe" not in out["sbt"]
    assert client.post("/api/export/submission", json={"ids": [item]}).json()["sbt"] is None
    assert client.post("/api/export/submission", json={"ids": [item], "submitter": "Nobody"}).status_code == 400
    path = tmp_path / "sub.tbl"
    client.post("/api/export/submission", json={"ids": [item], "submitter": "Fucus project", "path": str(path)})
    assert path.with_suffix(".sbt").exists()
