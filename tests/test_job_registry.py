import cladia.tool_plugins.mafft as owner_mafft
"""Engine declarations and internal job kinds cover every scheduled job."""
from cladia import jobs
from cladia.tool_registry import build_registry


def test_scientific_jobs_get_resources_from_engines(monkeypatch):
    registry = build_registry()
    scientific = {engine.get("jobKind", engine["id"]): (engine["lane"], engine["cores"])
                  for engine in registry.engines.values() if not engine.get("optional")}
    assert {"mafft", "tree", "mauve", "map", "reference", "orfs", "primers", "pcr",
            "blast", "localblast", "download", "assemble"} <= set(scientific)
    monkeypatch.setattr(jobs, "JOBS", {})
    monkeypatch.setattr(jobs, "_schedule", lambda: None)
    job = jobs.submit("mafft", "test", owner_mafft.run_alignment, items=[], output="test", strategy="auto", adjust_direction=True)
    assert (job["lane"], job["cores"]) == scientific["mafft"] == ("compute", None)
    assert jobs._queue[-1][1] is owner_mafft.run_alignment
    jobs._queue[:] = [entry for entry in jobs._queue if entry[0] is not job]
