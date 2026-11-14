"""An alignment owns its VCF through edits, history and portable projects."""
import hashlib
import zipfile

import pytest

from cladia import config, history, library, projects


@pytest.fixture
def alignment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "pair.fasta"
    source.write_text(">one\nACGT\n>two\nAGGT\n")
    return library.get_item(library.add_output(source, "pair", kind="alignment"))


VCF = ("##fileformat=VCFv4.3\n"
       "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tone\ttwo\n"
       "one\t2\t.\tC\tG\t.\tPASS\t.\tGT\t0\t1\n")


def test_alignment_vcf_attachment_and_portability(alignment, tmp_path):
    library.save_alignment_variants(alignment, VCF, "one")
    attached = library.get_item(alignment["id"])
    assert library.alignment_variants(attached) == {"vcf": VCF, "reference": "one", "stale": False}
    duplicate = library.get_item(library.duplicate(attached))
    assert library.alignment_variants(duplicate) == library.alignment_variants(attached)
    library.check_file_content(library.load()[0], library.library_dir())
    assert len(history.versions(f"{alignment['id']}_variants")["versions"]) == 2
    library.save_alignment_variants(attached, VCF.replace("PASS", "LowQual"), "one")
    assert len(history.versions(f"{alignment['id']}_variants")["versions"]) == 3
    target = tmp_path / "project.zip"
    projects.export_zip(target, include_history=True)
    with zipfile.ZipFile(target) as archive:
        assert f"library/{attached['variants']}" in archive.namelist()
        assert any(name.startswith(f"history/{alignment['id']}_variants/") for name in archive.namelist())
    projects.import_zip(target, name="Imported")
    imported = next(item for item in library.items() if item["kind"] == "alignment" and item["folder"] == "Imported")
    assert library.alignment_variants(imported)["vcf"] == VCF.replace("PASS", "LowQual")
    assert not library.alignment_variants(imported)["stale"]
    assert len(history.versions(f"{imported['id']}_variants")["versions"]) == 3
    with open(alignment["path"], "a", encoding="utf-8") as out:
        out.write("\n")
    assert library.alignment_variants(library.get_item(alignment["id"]))["stale"]


def test_alignment_vcf_rejects_wrong_reference_or_stale_job(alignment):
    with pytest.raises(ValueError, match="reference allele"):
        library.save_alignment_variants(alignment, VCF.replace("\tC\tG\t", "\tA\tG\t"), "one")
    with pytest.raises(ValueError, match="changed while"):
        library.save_alignment_variants(alignment, VCF, "one", expected_source_hash=hashlib.sha256(b"old").hexdigest())
    assert not library.get_item(alignment["id"]).get("variants")


def test_snp_plugin_attaches_calls_and_can_save_copy(alignment, tmp_path):
    from cladia.tool_plugins.alignment_snps import run

    job = {"outputs": [], "log": ""}
    run(job, tmp_path, "variants.alignment_snps", [alignment], "", reference_row="one")
    assert job["outputs"][0]["id"] == alignment["id"]
    assert job["outputs"][0]["updated"]
    assert "one\t2\t.\tC\tG\t.\tPASS\t.\tGT\t0\t1" in library.alignment_variants(
        library.get_item(alignment["id"]))["vcf"]

    copy_job = {"outputs": [], "log": ""}
    run(copy_job, tmp_path, "variants.alignment_snps", [alignment], "", reference_row="one", save_copy=True)
    copied = library.get_item(copy_job["outputs"][0]["id"])
    assert copied["id"] != alignment["id"]
    assert library.alignment_variants(copied) == library.alignment_variants(library.get_item(alignment["id"]))


def test_alignment_variant_plugin_calls_internal_insertions_and_deletions(tmp_path, monkeypatch):
    from cladia.tool_plugins.alignment_snps import run
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "gapped.fasta"
    source.write_text(">ref\nACG-TAC\n>insertion\nACGGTAC\n>deletion\nA-G-TAC\n")
    item = library.get_item(library.add_output(source, "gapped", kind="alignment"))
    job = {"outputs": [], "log": ""}
    run(job, tmp_path, "variants.alignment_snps", [item], "", reference_row="ref")
    rows = [line.split("\t") for line in library.alignment_variants(library.get_item(item["id"]))["vcf"].splitlines()
            if not line.startswith("#")]
    assert [(row[1], row[3], row[4]) for row in rows] == [("1", "AC", "A"), ("3", "G", "GG")]
    assert rows[0][9:] == ["0", "0", "1"]
    assert rows[1][9:] == ["0", "1", "0"]
    run({"outputs": [], "log": ""}, tmp_path, "variants.alignment_snps", [item], "",
        reference_row="ref", variant_types="snps")
    assert not [line for line in library.alignment_variants(library.get_item(item["id"]))["vcf"].splitlines()
                if not line.startswith("#")]


def test_alignment_variant_plugin_marks_repeat_indels(tmp_path, monkeypatch):
    from cladia.tool_plugins.alignment_snps import run
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "repeat.fasta"
    source.write_text(">ref\nAAAAAC\n>short\nAAA-AC\n")
    item = library.get_item(library.add_output(source, "repeat", kind="alignment"))
    run({"outputs": [], "log": ""}, tmp_path, "variants.alignment_snps", [item], "", reference_row="ref")
    rows = [line for line in library.alignment_variants(library.get_item(item["id"]))["vcf"].splitlines()
            if not line.startswith("#")]
    assert len(rows) == 1
    assert "TYPE=INDEL;RU=A" in rows[0]


def test_terminal_gap_is_missing_call_not_deletion(tmp_path, monkeypatch):
    from cladia.tool_plugins.alignment_snps import run
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "terminal.fasta"
    source.write_text(">ref\nACGT\n>short\n-CGT\n>changed\nTCGT\n")
    item = library.get_item(library.add_output(source, "terminal", kind="alignment"))
    run({"outputs": [], "log": ""}, tmp_path, "variants.alignment_snps", [item], "", reference_row="ref")
    rows = [line.split("\t") for line in library.alignment_variants(library.get_item(item["id"]))["vcf"].splitlines()
            if not line.startswith("#")]
    assert len(rows) == 1
    assert rows[0][1:5] == ["1", ".", "A", "T"]
    assert rows[0][9:] == ["0", ".", "1"]


@pytest.mark.parametrize("reference,sample,expected", [
    ("A-C", "GTC", [(1, "A", "G"), (2, "C", "TC")]),
    ("ATC", "G-C", [(1, "A", "G"), (2, "TC", "C")]),
    ("--ATC", "G--TC", [(1, "AT", "T")]),
    ("--ATC", "G--GC", [(1, "AT", "G")]),
    ("A-C", "GTG", [(1, "A", "GT"), (2, "C", "G")]),
    ("A--C", "G--C", [(1, "A", "G")]),
    ("A-C", "NTC", [(2, "C", "TC")]),
])
def test_indels_preserve_adjacent_snps_and_valid_reference_coordinates(tmp_path, monkeypatch, reference, sample, expected):
    from cladia.tool_plugins.alignment_snps import run
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    source = tmp_path / "edge.fasta"
    source.write_text(f">ref\n{reference}\n>sample\n{sample}\n")
    item = library.get_item(library.add_output(source, "edge", kind="alignment"))
    job = {"outputs": [], "log": ""}
    run(job, tmp_path, "variants.alignment_snps", [item], "", reference_row="ref")
    # Publishing checks every VCF REF against the reference, not just the pure caller.
    saved = library.alignment_variants(library.get_item(item["id"]))
    rows = [line.split("\t") for line in saved["vcf"].splitlines() if not line.startswith("#")]
    assert [(int(row[1]), row[3], row[4]) for row in rows] == expected
    assert all(row[9] == "0" and row[10] == "1" for row in rows)
    assert not saved["stale"] and job["outputs"][0]["updated"]
