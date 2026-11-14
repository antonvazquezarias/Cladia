import cladia.tool_plugins.iqtree as owner_iqtree
import cladia.tool_plugins.mafft as owner_mafft
import cladia.tool_plugins.mauve as owner_mauve
import cladia.tool_plugins.primer3 as owner_primer3
from cladia import config, jobs, library, run_details


def test_updated_sequence_keeps_details_after_log_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "data" / ".cladia")
    library.ensure_folders()
    ids, errors = library.import_text("source.fa", ">source\nACGTACGT\n")
    assert not errors
    item = library.get_item(ids[0])

    def annotate(job, workdir, items, minimum=100):
        job["outputs"].append({"id": items[0]["id"], "label": items[0]["label"], "updated": True})

    params = {"items": [item]}
    job = {"id": "example", "kind": "primers", "title": "Example", "log": "", "outputs": [],
           "_run_details": run_details.capture("primers", annotate, params)}
    jobs._run(job, annotate, params)
    saved = jobs.saved_log("example")
    assert saved["status"] == "done"
    assert '"minimum": 100' in saved["log"]
    assert "Untergasser et al. (2012)" in saved["log"]
    (jobs.logs_dir() / "example.json").unlink()
    provenance = library.get_item(item["id"])["provenance"]
    assert provenance["parameters"]["minimum"] == 100
    assert provenance["citations"][0]["tool"] == "Primer3"


def test_failed_job_retains_parameters_and_citations(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")

    def fail(job, workdir, strategy="auto"):
        raise RuntimeError("failed deliberately")

    details = run_details.capture("mafft", fail, {})
    job = {"id": "failure", "kind": "mafft", "title": "Example", "log": "", "outputs": [], "_run_details": details}
    jobs._run(job, fail, {})
    saved = jobs.saved_log("failure")
    assert saved["status"] == "failed" and saved["error"] == "failed deliberately"
    assert '"strategy": "auto"' in saved["log"]
    assert "10.1093/molbev/mst010" in saved["log"]


def test_citations_follow_selected_methods_and_actual_iqtree_version():
    def dois(kind, func, **params):
        details = run_details.capture(kind, func, params)
        return {c["url"] for c in details["citations"]}

    assert "https://doi.org/10.1093/bioinformatics/btm091" in dois("primers", owner_primer3.run_design)
    add_paper = "https://doi.org/10.1093/bioinformatics/bts578"
    assert add_paper in dois("mafft", owner_mafft.run_alignment, add_to={"id": "x", "label": "reference"})
    assert add_paper not in dois("mafft", owner_mafft.run_alignment, add_to=None)
    blast_plus = "https://doi.org/10.1186/1471-2105-10-421"
    assert blast_plus in dois("mauve", owner_mauve.run_comparison, line_up=True)
    assert blast_plus not in dois("mauve", owner_mauve.run_comparison, line_up=False)
    model = "https://doi.org/10.1038/nmeth.4285"
    bootstrap = "https://doi.org/10.1093/molbev/msx281"
    assert {model, bootstrap} <= dois("tree", owner_iqtree.run_tree, program="iqtree", model="auto", bootstrap=True)
    assert not {model, bootstrap} & dois("tree", owner_iqtree.run_tree, program="iqtree", model="GTR", bootstrap=False)
    assert not {model, bootstrap} & dois("tree", owner_iqtree.run_tree, program="fasttree", model="auto", bootstrap=True)

    for number, doi in [("2", "https://doi.org/10.1093/molbev/msaa015"), ("3", "https://doi.org/10.1093/molbev/msag117")]:
        details = run_details.capture("tree", owner_iqtree.run_tree, {"program": "iqtree"})
        job = {"kind": "tree", "log": f"IQ-TREE multicore version {number}.1.0\n", "_run_details": details}
        run_details.observed_versions(job)
        assert details["citations"][0]["url"] == doi
    details = run_details.capture("tree", owner_iqtree.run_tree, {"program": "iqtree"})
    run_details.observed_versions({"kind": "tree", "log": "No version reported", "_run_details": details})
    assert all(c["url"] not in {v[2] for v in run_details.IQ_VERSIONS.values()} for c in details["citations"])


def test_engine_defaults_capture_method_and_mapping_units():
    from cladia.tool_plugins import fasttree, minimap2
    details = run_details.capture("tree", fasttree.run, {})
    assert details["parameters"]["program"] == "fasttree"
    assert [c["tool"] for c in details["citations"]] == ["FastTree"]
    details = run_details.capture("map", minimap2.run, {})
    assert details["parameters"]["read_type"] == "ont_hq"
    assert details["parameters"]["min_agree"] == 80
    direct = run_details.capture("map", minimap2.run_mapping, {})
    assert direct["parameters"]["min_depth"] == 3
    assert direct["parameters"]["min_agree"] == 0.8
