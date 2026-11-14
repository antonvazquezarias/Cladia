"""Metadata from a table: CSV / TSV / Excel read, rows matched to sequences by name, fields filled."""
import base64
import datetime
import io

import pytest
from fastapi.testclient import TestClient

from cladia import config, library, metatable

FASTA = ">Fuc_01\nACGTACGTACGT\n>Fuc_02\nACGTTTGGAACC\n>Fuc_03\nGGGGCCCCAAAA\n"


def test_table_limit_is_an_error_not_silent_truncation(monkeypatch):
    monkeypatch.setattr(metatable, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="exceeds"):
        metatable.read_table("table.csv", b"id,host\na,x\nb,y\n")


def test_reads_csv_with_semicolons_tsv_and_odd_headers():
    columns, rows = metatable.read_table("a.csv", "Sample;Country;;Country\nFuc 01;Spain;x;Italy\n\nFuc 02;;y;\n".encode())
    assert columns == ["Sample", "Country", "Column 3", "Country (2)"]
    assert rows == [["Fuc 01", "Spain", "x", "Italy"], ["Fuc 02", "", "y", ""]]
    columns, rows = metatable.read_table("a.tsv", "id\thost\nA\tAscophyllum, nodosum\n".encode("utf-16"))  # (a BOM'd UTF-16 file)
    assert columns == ["id", "host"] and rows == [["A", "Ascophyllum, nodosum"]]
    with pytest.raises(ValueError):
        metatable.read_table("a.csv", b"only a header\n")


def test_reads_excel_with_dates_and_numbers():
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(["Sample", "Collected", "Depth", "Voucher"])
    sheet.append(["Fuc_01", datetime.datetime(2023, 6, 5), 12.0, "V-1"])
    sheet.append(["Fuc_02", None, 3.5, None])
    out = io.BytesIO()
    book.save(out)
    columns, rows = metatable.read_table("t.xlsx", out.getvalue())
    assert columns == ["Sample", "Collected", "Depth", "Voucher"]
    assert rows == [["Fuc_01", "2023-06-05", "12", "V-1"], ["Fuc_02", "", "3.5", ""]]
    with pytest.raises(ValueError, match="old Excel"):
        metatable.read_table("old.xls", b"\xd0\xcf\x11\xe0 rest")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    return TestClient(app)


def test_ambiguity_stale_preview_and_large_list_history(client, monkeypatch):
    parent = client.post("/api/library/upload", json={"filename": "fuc.fa", "text": FASTA}).json()["ids"][0]
    def request(text):
        token = client.post("/api/metadata/table", json={"filename": "t.csv", "text": text}).json()["token"]
        return {"ids": [parent, parent + "-0"], "token": token, "name_column": 0, "mapping": {"host": 1}}
    ambiguous = request("id,host\nFuc_01,a\nfuc 01,b\nFuc_02,c\n")
    preview = client.post("/api/library/import-metadata", json=ambiguous).json()
    assert preview["ambiguous_count"] == 1 and preview["matched"] == 1
    assert client.post("/api/library/import-metadata", json={**ambiguous, "apply": True}).status_code == 400
    assert library.record_metadata(library.get_item(parent + "-1"))["host"] == ""
    good = request("id,host\nFuc_01,a\nFuc_02,b\n")
    preview = client.post("/api/library/import-metadata", json=good).json()
    assert preview["changed"] == 2  # overlapping list + child selection is counted once
    library.edit_metadata(library.get_item(parent + "-0"), {"host": "changed elsewhere"})
    assert client.post("/api/library/import-metadata", json={**good, "expected_hash": preview["preview_hash"], "apply": True}).status_code == 409
    monkeypatch.setattr(library, "BIG_LIST", 1)
    preview = client.post("/api/library/import-metadata", json=good).json()
    applied = client.post("/api/library/import-metadata", json={**good, "expected_hash": preview["preview_hash"], "apply": True})
    assert applied.status_code == 200, applied.text
    child = library.get_item(parent + "-0")
    assert not library.saved_versions(child)["whole_list"]
    library.restore_version(child, 0)
    assert library.record_metadata(library.get_item(child["id"]))["host"] == "changed elsewhere"


def test_preview_counts_every_change_not_just_sample(client):
    parent = client.post("/api/library/upload", json={"filename": "many.fa", "text": "".join(f">s{i}\nACGT\n" for i in range(10))}).json()["ids"][0]
    text = "id,host\n" + "".join(f"s{i},host{i}\n" for i in range(10))
    token = client.post("/api/metadata/table", json={"filename": "t.csv", "text": text}).json()["token"]
    req = {"ids": [parent], "token": token, "name_column": 0, "mapping": {"host": 1}}
    preview = client.post("/api/library/import-metadata", json=req).json()
    assert preview["changed"] == 10 and len(preview["sample"]) == 6


def test_accession_alias_collision_is_rejected(client):
    item = client.post("/api/library/upload", json={"filename": "x.fa", "text": ">AB123.1\nACGT\n"}).json()["ids"][0]
    token = client.post("/api/metadata/table", json={"filename": "t.csv", "text": "id,host\nAB123.1,a\nAB123.2,b\n"}).json()["token"]
    req = {"ids": [item], "token": token, "name_column": 0, "mapping": {"host": 1}}
    assert client.post("/api/library/import-metadata", json=req).json()["ambiguous_count"] == 1
    assert client.post("/api/library/import-metadata", json={**req, "apply": True}).status_code == 400


def test_history_write_failure_still_allows_recovery(client, monkeypatch):
    from cladia import history
    item_id = client.post("/api/library/upload", json={"filename": "a.fa", "text": ">sample\nACGT\n"}).json()["ids"][0]
    original = history.checkpoint
    calls = 0
    def fail_after_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3: raise OSError("injected history failure")
        return original(*args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(history, "checkpoint", fail_after_write)
        with pytest.raises(ValueError, match="Metadata import stopped"):
            library.import_table_metadata([item_id], [["sample", "Alga"]], 0, {"host": 1}, apply=True)
    library.restore_version(library.get_item(item_id), 0)
    assert library.record_metadata(library.get_item(item_id))["host"] == ""


def test_multi_file_metadata_failure_rolls_back_all_files(client, monkeypatch):
    first = client.post("/api/library/upload", json={"filename": "a.fa", "text": ">first\nACGT\n"}).json()["ids"][0]
    second = client.post("/api/library/upload", json={"filename": "b.fa", "text": ">second\nACGA\n"}).json()["ids"][0]
    original = library.save_record
    def fail_second(item, record):
        if item["id"] == second: raise OSError("injected failure")
        return original(item, record)
    monkeypatch.setattr(library, "save_record", fail_second)
    with pytest.raises(ValueError, match="rolled back"):
        library.import_table_metadata([first, second], [["first", "Alga"], ["second", "Seaweed"]], 0, {"host": 1}, apply=True)
    assert [library.record_metadata(library.get_item(i))["host"] for i in (first, second)] == ["", ""]


def test_failed_save_does_not_prune_previous_versions(client, monkeypatch):
    from cladia import history
    item_id = client.post("/api/library/upload", json={"filename": "a.fa", "text": ">sample\nACGT\n"}).json()["ids"][0]
    library.edit_metadata(library.get_item(item_id), {"host": "first host"})
    timeline = history.versions(item_id)
    original = history._save
    monkeypatch.setattr(history, "MAX_VERSIONS", 2)
    def fail_new(folder, data):
        current = data["versions"][data["current"]]["hash"]
        if current != timeline["versions"][timeline["current"]]["hash"]:
            raise OSError("history index failed")
        return original(folder, data)
    with monkeypatch.context() as scoped:
        scoped.setattr(history, "_save", fail_new)
        with pytest.raises(OSError, match="history index failed"):
            library.edit_metadata(library.get_item(item_id), {"host": "second host"})
    assert history.versions(item_id) == timeline
    library.restore_version(library.get_item(item_id), 0)
    assert library.record_metadata(library.get_item(item_id))["host"] == ""


def test_save_api_reports_disk_failure_and_keeps_original(client, monkeypatch):
    from cladia import history
    item_id = client.post("/api/library/upload", json={"filename": "a.fa", "text": ">sample\nACGT\n"}).json()["ids"][0]
    original, calls = history.checkpoint, 0
    def fail_new(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError("disk full")
        return original(*args, **kwargs)
    monkeypatch.setattr(history, "checkpoint", fail_new)
    response = client.post(f"/api/library/{item_id}/metadata", json={"host": "Alga"})
    assert response.status_code == 507 and "disk full" in response.json()["detail"]
    assert library.record_metadata(library.get_item(item_id))["host"] == ""


def test_import_metadata_from_a_table(client):
    up = client.post("/api/library/upload", json={"filename": "fuc.fasta", "text": FASTA}).json()
    assert len(up["ids"]) == 1  # one list of three
    list_id = up["ids"][0]
    table = ("Sample,Country,Collected,Host,Voucher\n"
             "fuc 01,Spain,2023-06-05,Ascophyllum,V-1\n"      # spaces and case: still Fuc_01
             "Fuc_02,,2022,,V-2\n"                            # empty cells: left alone
             "Other,Italy,2020,x,V-9\n")                      # matches nothing
    read = client.post("/api/metadata/table", json={"filename": "t.csv", "text": table, "ids": [list_id]}).json()
    assert read["columns"][0] == "Sample" and read["count"] == 3
    assert read["matches"] == [2, 0, 0, 0, 0]                 # the first column holds the names
    assert {"key": "lat_lon", "label": "Latitude and longitude"} in read["fields"]
    request = {"ids": [list_id], "token": read["token"], "name_column": 0,
               "mapping": {"country": 1, "collection_date": 2, "host": 3, "specimen_voucher": 4}}
    preview = client.post("/api/library/import-metadata", json=request).json()
    assert preview["applied"] is False and preview["matched"] == 2 and preview["unmatched"] == ["Fuc_03"]
    assert preview["unused"] == ["Other"] and preview["sample"][0]["changes"][0][:1] == ["country"]
    members = [i for i in client.get("/api/library").json() if i.get("parent") == list_id]
    meta = lambda name: client.get(f"/api/library/{next(m for m in members if m['name'] == name)['id']}/metadata").json()
    assert meta("Fuc_01")["country"] == ""                     # a preview changes nothing
    done = client.post("/api/library/import-metadata", json={**request, "apply": True}).json()
    assert done["applied"] and done["changed"] == 2 and done["values"] == 6  # 4 + 2 (Fuc_02 has no country or host)
    one, two, three = meta("Fuc_01"), meta("Fuc_02"), meta("Fuc_03")
    assert (one["country"], one["collection_date"], one["host"]) == ("Spain", "2023-06-05", "Ascophyllum")
    assert one["specimen_voucher"] == "V-1" and two["specimen_voucher"] == "V-2" and two["country"] == ""
    assert three["country"] == ""                              # not in the table: untouched
    # the list still has its three sequences, with their bases
    assert [m["name"] for m in sorted(members, key=lambda m: m["record"])] == ["Fuc_01", "Fuc_02", "Fuc_03"]
    # again: nothing changes; with "empty cells clear", Fuc_02's empty host would be cleared (it had none: still nothing)
    again = client.post("/api/library/import-metadata", json={**request, "apply": True}).json()
    assert again["changed"] == 0
    client.post("/api/library/import-metadata", json={**request, "mapping": {"host": 3}, "clear_empty": True, "apply": True})
    client.post("/api/library/import-metadata", json={**request, "mapping": {"host": 3}, "clear_empty": True, "apply": True})
    assert meta("Fuc_01")["host"] == "Ascophyllum" and meta("Fuc_02")["host"] == ""


def test_errors(client):
    up = client.post("/api/library/upload", json={"filename": "fuc.fasta", "text": FASTA}).json()
    read = client.post("/api/metadata/table", json={"filename": "t.csv", "text": "a,b\n1,2\n", "ids": up["ids"]}).json()
    base = {"ids": up["ids"], "token": read["token"], "name_column": 0, "mapping": {"host": 1}}
    assert client.post("/api/library/import-metadata", json={**base, "token": "nope"}).status_code == 409
    assert client.post("/api/library/import-metadata", json={**base, "name_column": 7}).status_code == 400
    assert client.post("/api/library/import-metadata", json={**base, "mapping": {"nonsense": 1}}).status_code == 400
    assert client.post("/api/metadata/table", json={"filename": "t.csv", "text": "only,a,header"}).status_code == 400
    data = base64.b64encode(b"x").decode()
    assert client.post("/api/metadata/table", json={"filename": "t.xlsx", "data": data}).status_code == 400
