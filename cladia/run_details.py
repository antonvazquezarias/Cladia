"""Human-readable job settings and citations retained with results and logs."""
import hashlib
import inspect
import re
from pathlib import Path

# Links go to the authors' citation guidance, which may include method-specific papers.
CITATIONS = {
    "mafft": [("MAFFT", "Katoh and Standley (2013)", "https://doi.org/10.1093/molbev/mst010")],
    "mauve": [("progressiveMauve", "Darling et al. (2010)", "https://doi.org/10.1371/journal.pone.0011147")],
    "iqtree": [("IQ-TREE", "Version and feature-specific citations", "https://www.iqtree.org/doc/Home#how-to-cite-iq-tree")],
    "fasttree": [("FastTree", "Price et al. (2010)", "https://doi.org/10.1371/journal.pone.0009490")],
    "map": [("minimap2", "Li (2018)", "https://doi.org/10.1093/bioinformatics/bty191"),
            ("SAMtools", "Danecek et al. (2021)", "https://doi.org/10.1093/gigascience/giab008")],
    "getorganelle": [("GetOrganelle", "Jin et al. (2020)", "https://doi.org/10.1186/s13059-020-02154-5")],
    "primers": [("Primer3", "Untergasser et al. (2012)", "https://doi.org/10.1093/nar/gks596"),
                ("Primer3", "Koressaar and Remm (2007)", "https://doi.org/10.1093/bioinformatics/btm091")],
    "blast": [("BLAST", "Altschul et al. (1990)", "https://doi.org/10.1016/S0022-2836(05)80360-2"),
              ("BLAST references", "NCBI method-specific references", "https://blast.ncbi.nlm.nih.gov/doc/blast-help/references.html")],
}
BLAST_PLUS = ("BLAST+", "Camacho et al. (2009)", "https://doi.org/10.1186/1471-2105-10-421")
IQ_VERSIONS = {
    "1": ("IQ-TREE 1", "Nguyen et al. (2015)", "https://doi.org/10.1093/molbev/msu300"),
    "2": ("IQ-TREE 2", "Minh et al. (2020)", "https://doi.org/10.1093/molbev/msaa015"),
    "3": ("IQ-TREE 3", "Wong et al. (2026)", "https://doi.org/10.1093/molbev/msag117"),
}


def _citation(entry):
    tool, reference, url = entry
    return {"tool": tool, "reference": reference, "url": url}


def _value(value):
    if isinstance(value, dict):
        if "id" in value and "label" in value:
            return {"id": value["id"], "label": value["label"]}
        return {str(k): ({"length": len(v), "sha256": hashlib.sha256(v.encode("utf-8")).hexdigest()}
                         if str(k).lower() == "sequence" and isinstance(v, str) else _value(v)) for k, v in value.items()
                if str(k).lower() not in {"token", "password", "api_key", "busy"}}
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "id") and hasattr(value, "seq"):
        return {"id": value.id, "length": len(value.seq)}
    return str(type(value).__name__)


def _engine_citations(kind):
    """What an engine declares (ENGINE["citations"]) for its job kind."""
    try:
        from .tool_registry import build_registry
        engine = next((e for e in build_registry().engines.values() if e.get("jobKind", e["id"]) == kind), {})
    except Exception:
        return []
    return [tuple(c) for c in engine.get("citations", [])]


def capture(kind, func, params):
    parameters = dict(params)
    try:
        bound = inspect.signature(func).bind_partial(None, None, **params)
        bound.apply_defaults()
        parameters = {k: v for k, v in bound.arguments.items() if k not in ("job", "workdir", "busy")}
    except (TypeError, ValueError):
        pass
    key = (parameters.get("program") or "iqtree").lower() if kind == "tree" else kind
    if kind in ("localblast", "graphblast", "blastdb", "reference"):
        key = "blast"
    citations = list(CITATIONS.get(key, []) or _engine_citations(kind))
    if kind == "mafft" and parameters.get("add_to"):
        citations.append(("MAFFT add sequences", "Katoh and Frith (2012)", "https://doi.org/10.1093/bioinformatics/bts578"))
    if kind == "mauve" and parameters.get("line_up", True):
        citations.extend([*CITATIONS["blast"], BLAST_PLUS])
    if kind in ("localblast", "graphblast", "blastdb", "reference"):
        citations.append(BLAST_PLUS)
    if key == "iqtree":
        if parameters.get("model", "auto") == "auto":
            citations.append(("ModelFinder", "Kalyaanamoorthy et al. (2017)", "https://doi.org/10.1038/nmeth.4285"))
        if parameters.get("bootstrap", True):
            citations.append(("UFBoot2", "Hoang et al. (2018)", "https://doi.org/10.1093/molbev/msx281"))
    return {"parameters": _value(parameters), "citations": [_citation(c) for c in citations],
        "versions": "Not recorded"}


def observed_versions(job):
    """Use only versions stated by the program; installed versions may differ."""
    patterns = {
        "mafft": r"(?im)^\s*(v7\.\d+(?:\.\d+)?)\b",
        "tree": r"(?im)^\s*(IQ-TREE[^\n]*?version\s+[\w.\-]+)",
    }
    match = re.search(patterns.get(job["kind"], r"(?!)"), job.get("log", ""))
    if match:
        job.setdefault("_run_details", {})["versions"] = match.group(1).strip()
        if job["kind"] == "tree":
            version = re.search(r"\bversion\s+([123])(?:\.|\b)", match.group(1), re.I)
            if version:
                job["_run_details"].setdefault("citations", []).insert(0, _citation(IQ_VERSIONS[version.group(1)]))


def record_tool(job, name, version, citations=()):
    """Record an engine's observed version and citations without engine-specific host code."""
    if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
        raise ValueError("Tool name and observed version are required")
    entries = []
    for entry in citations:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key].strip()
                                              for key in ("tool", "reference", "url")):
            raise ValueError("A citation needs tool, reference and URL")
        entries.append({key: entry[key] for key in ("tool", "reference", "url")})
    details = job.setdefault("_run_details", {})
    versions = details.setdefault("tool_versions", {})
    versions[name] = version.strip()
    details["versions"] = "; ".join(f"{tool} {value}" for tool, value in versions.items())
    recorded = details.setdefault("citations", [])
    recorded.extend(entry for entry in entries if entry not in recorded)


def append_log(job):
    import json
    details = job.get("_run_details", {})
    citations = details.get("citations", [])
    job["log"] += "\n── Run details ──\n" + json.dumps(details.get("parameters", {}), indent=2, ensure_ascii=False)
    job["log"] += "\nTool versions: " + details.get("versions", "Not recorded")
    if details.get("commands"):
        job["log"] += "\nCommands attempted:\n" + "\n".join(details["commands"])
    job["log"] += "\n\n── Citations ──\n" + ("\n".join(f"{c['tool']}: {c['reference']} — {c['url']}" for c in citations)
        if citations else "No external tool citation recorded for this job.") + "\n"
