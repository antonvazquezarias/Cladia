"""Distance trees (neighbour-joining or UPGMA), computed inside Cladia.

Nothing to download: distances and the tree are numpy code (cladia/distance_tree.py).
Quick, for a first look or for many sequences; no model of how sites evolve
beyond the distance's correction.
"""
from cladia import engine_api as api

ENGINE = {
    "id": "distance_tree", "label": "Neighbour-joining / UPGMA", "order": 40,
    "blurb": "Quick distance trees: neighbour-joining, or UPGMA (rooted, equal tip heights), with optional bootstrap.",
    "supports": ["tree.build"], "lane": "compute", "cores": 1, "jobKind": "distancetree",
    "storage": {"kind": "builtin"},
    "options": [
        {"key": "method", "label": "Method", "type": "choice", "default": "nj", "choices": [
            {"value": "nj", "label": "Neighbour-joining"}, {"value": "upgma", "label": "UPGMA"}]},
        {"key": "distance", "label": "Distance", "type": "choice", "default": "auto", "choices": [
            {"value": "auto", "label": "Kimura 2-parameter (DNA) or Poisson (proteins)"},
            {"value": "p", "label": "p-distance (uncorrected)"},
            {"value": "jc", "label": "Jukes-Cantor (DNA)"},
            {"value": "k2p", "label": "Kimura 2-parameter (DNA)"},
            {"value": "poisson", "label": "Poisson (proteins)"}]},
        {"key": "bootstrap", "label": "Bootstrap replicates", "type": "int", "default": 100, "min": 0, "max": 10000,
         "help": "0 for no supports."},
        {"key": "outgroup", "label": "Outgroup (neighbour-joining)", "type": "choice", "default": "",
         "choices": [{"value": "", "label": "None (unrooted)"}], "choicesFrom": {"slot": "alignment", "kind": "alignment_rows"}},
    ],
}
METHOD_CITATIONS = {
    "nj": {"tool": "Neighbour-joining", "reference": "Saitou & Nei (1987), Molecular Biology and Evolution 4:406",
           "url": "https://doi.org/10.1093/oxfordjournals.molbev.a040454"},
    "upgma": {"tool": "UPGMA", "reference": "Sokal & Michener (1958), University of Kansas Science Bulletin 38:1409",
              "url": "https://archive.org/details/cbarchive_33927_astatisticalmethodforevaluatingsy1902"},
}
DISTANCE_CITATIONS = {
    "jc": {"tool": "Jukes-Cantor distance", "reference": "Jukes & Cantor (1969), Mammalian Protein Metabolism 3:21",
           "url": "https://doi.org/10.1016/B978-1-4832-3211-9.50009-7"},
    "k2p": {"tool": "Kimura 2-parameter distance", "reference": "Kimura (1980), Journal of Molecular Evolution 16:111",
            "url": "https://doi.org/10.1007/BF01731581"},
}
BOOTSTRAP = {"tool": "Bootstrap", "reference": "Felsenstein (1985), Evolution 39:783", "url": "https://doi.org/10.2307/2408678"}


def run(job, workdir, operation, items, output, add_to=None, method="nj", distance="auto", bootstrap=100,
        outgroup="", program=None):
    if operation != "tree.build" or len(items) != 1:
        raise ValueError("A distance tree is built from one alignment")
    records, renamed = api.tree_rows(items[0])
    protein = api.looks_protein("".join(str(r.seq) for r in records))
    api.steps(job, "Distances and tree", *(["Bootstrapping"] if bootstrap else []))
    newick, notes = api.distance_tree(
        [r.id for r in records], [str(r.seq) for r in records], method=method, distance=distance,
        bootstrap=bootstrap, outgroup=renamed.get(outgroup) if outgroup else None, protein=protein,
        cancelled=lambda: bool(job.get("cancel")),
        progress=lambda fraction: api.step(job, 1, fraction, f"{round(fraction * bootstrap)} of {bootstrap} replicates"))
    job["log"] += "\n".join(notes) + "\n"
    kind = "k2p" if distance == "auto" and not protein else distance
    api.record_tool(job, METHOD_CITATIONS[method]["tool"], "built into Cladia",
                    [METHOD_CITATIONS[method], *([DISTANCE_CITATIONS[kind]] if kind in DISTANCE_CITATIONS else []),
                     *([BOOTSTRAP] if bootstrap else [])])
    (workdir / "tree.nwk").write_text(newick + "\n", encoding="utf-8")
    api.add_output(job, workdir / "tree.nwk", output, kind="tree")
