"""Submission validation uses request-local snapshots, without stale global caches."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cladia import config, library, tool_validation
from cladia.item_inputs import InputError


@pytest.fixture
def private_library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    return tmp_path


def request(operation, engine, inputs, **options):
    return SimpleNamespace(operation=operation, engine=engine, inputs=inputs, options=options)


def test_add_alignment_uses_one_library_snapshot(private_library, monkeypatch):
    ids, errors = library.import_text("sequences.fa", ">one\nACGT\n>two\nACGA\n")
    assert not errors
    members = [it["id"] for it in library.items() if it.get("parent") == ids[0]]
    scans = Mock(wraps=library.items)
    monkeypatch.setattr(library, "items", scans)
    req = request("align.add", "mafft", {"sequences": ids, "add_to": [members[0]]})
    _, _, resolved, items, _ = tool_validation.validate_tool_request(req)
    assert scans.call_count == 1
    assert [it["id"] for it in resolved["sequences"]] == members
    assert [it["id"] for it in items] == [members[1]]


def test_tree_count_and_outgroup_share_alignment_parse(private_library, monkeypatch):
    ids, errors = library.import_text("tree.fa", ">one\nACG-T\n>two\nACGAT\n>three\nACGGT\n")
    assert not errors
    reads = Mock(wraps=tool_validation.read_records)
    monkeypatch.setattr(tool_validation, "read_records", reads)
    req = request("tree.build", "iqtree", {"alignment": ids}, outgroup="one")
    assert tool_validation.validate_tool_request(req)[-1]["outgroup"] == "one"
    assert reads.call_count == 1


def test_alignment_row_limit_belongs_to_operation(private_library, monkeypatch):
    from cladia import tool_registry
    source = private_library / "pair.fa"
    source.write_text(">one\nACGT\n>two\nACGA\n")
    ids = [library.add_output(source, "pair", kind="alignment")]
    with pytest.raises(InputError, match="Build tree needs at least 3 alignment rows"):
        tool_validation.validate_tool_request(request("tree.build", "iqtree", {"alignment": ids}))
    registry = tool_registry.build_registry(installed={})
    registry.registerOperation({"id": "variants.detect", "category": "variants", "label": "Detect variants",
                                "description": "", "order": 10,
                                "inputs": [{"key": "alignment", "label": "Alignment", "docType": "alignment",
                                            "min": 1, "max": 1, "minRows": 2}]})
    registry.registerEngine({"id": "test_variants", "label": "Test variants", "blurb": "",
                             "supports": ["variants.detect"], "options": [], "lane": "compute", "cores": 1},
                            lambda *args: None)
    monkeypatch.setattr(tool_registry, "build_registry", lambda: registry)
    resolved = tool_validation.validate_tool_request(request("variants.detect", "test_variants", {"alignment": ids}))[2]
    assert resolved["alignment"][0]["id"] == ids[0]


def test_download_does_not_scan_library(private_library, monkeypatch):
    scans = Mock(side_effect=AssertionError("Download has no library input"))
    monkeypatch.setattr(library, "items", scans)
    req = request("download.accessions", "genbank_download", {"accessions": ["NC_000001"]})
    assert tool_validation.validate_tool_request(req)[2]["accessions"] == ["NC_000001"]
    scans.assert_not_called()


@pytest.mark.parametrize("operation,engine,inputs", [
    ("align.new", "mafft", {"sequences": [{}]}),
    ("map.reads", "minimap2", {"reference": [123], "read_files": ["reads.fq"]}),
])
def test_invalid_item_ids_are_validation_errors(private_library, operation, engine, inputs):
    with pytest.raises(InputError) as caught:
        tool_validation.validate_tool_request(request(operation, engine, inputs))
    assert caught.value.status_code == 400


def test_invalid_read_path_is_a_validation_error(private_library):
    ids, errors = library.import_text("reference.fa", ">reference\nACGT\n")
    assert not errors
    req = request("map.reads", "minimap2", {"reference": ids, "read_files": [123]})
    with pytest.raises(InputError, match="Invalid read file path"):
        tool_validation.validate_tool_request(req)


def test_graph_blast_batch_reads_each_graph_once(private_library, monkeypatch):
    from cladia import jobs, tool_jobs
    ids, errors = library.import_text("graph.gfa", "S\ta\tACGT\nS\tb\tACGA\n")
    assert not errors
    graphs = Mock(wraps=library.graph_data)
    scans = Mock(wraps=library.item_map)
    submit = Mock(return_value={"id": "job"})
    monkeypatch.setattr(library, "graph_data", graphs)
    monkeypatch.setattr(library, "item_map", scans)
    monkeypatch.setattr(jobs, "submit", submit)
    queries = [{"item": ids[0], "node": name} for name in ("a", "b")]
    req = SimpleNamespace(context={}, operation="blast.search", output="hits")
    options = {"program": "blastn", "database": "core_nt", "max_hits": 10}
    engine = {"id": "ncbi_blast", "run": lambda: None, "lane": "blast", "cores": 0}
    assert len(tool_jobs.submit_blast(req, {"queries": queries}, [], engine, options, None)) == 2
    assert scans.call_count == graphs.call_count == 1
    assert [call.kwargs["query"]["sequence"] for call in submit.call_args_list] == ["ACGT", "ACGA"]
