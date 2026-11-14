"""Graph viewing, editing and comparison routes."""
from .request_context import ResultDestination, _free, _item, _record_created, _result_folder, _sequences
from . import internal_jobs
import re
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from fastapi import HTTPException
from pydantic import BaseModel
from . import graph, jobs, library
from .genbank import record_to_editor

from fastapi import APIRouter

router = APIRouter()

def _graph(item_id):
    item = _item(item_id)
    if item["kind"] != "graph":
        raise HTTPException(400, f"{item['label']} is not an assembly graph")
    return item, library.graph_data(item)


@router.get("/api/graph/{item_id}")
def get_graph(item_id: str):
    """The graph for the viewer: nodes (no sequences), links, paths."""
    item, data = _graph(item_id)
    return {**graph.view(data), "hits": library.graph_hits(item)}


class GraphBlastRequest(BaseModel):
    queries: list[str]  # sequence or list items
    task: str = "blastn"


@router.post("/api/graph/{item_id}/blast")
def blast_graph(item_id: str, req: GraphBlastRequest):
    """A reference compared with the graph: BLAST against its nodes (a job); the hits go beside the graph."""
    item, _ = _graph(item_id)
    queries = [_item(i) for i in req.queries]
    if len(queries) != 1 or queries[0]["kind"] != "sequence":
        raise HTTPException(400, "Pick one sequence (a related genome) to compare the graph with")
    if req.task not in internal_jobs.GRAPH_TASKS:
        raise HTTPException(400, f"Unknown search: {req.task}")
    if any(library.item_is_protein(q) for q in queries):
        raise HTTPException(400, "The nodes are DNA: search them with DNA sequences")
    name = queries[0]["name"] if len(queries) == 1 else f"{len(queries)} items"
    return jobs.submit("graphblast", f"Compare {item['name']} with {name}", internal_jobs.graph_blast_job,
                       graph_item=item, queries=queries, task=req.task)


@router.get("/api/graph/{item_id}/tracks")
def graph_tracks(item_id: str, min_length: int = 0, min_depth: float = 0, only_hits: bool = False, walks: str = ""):
    """The graph as tracks for the sequence viewer (graph.tracks, graph.track_view):
    a genome comparison's shape, the reference compared with it (if any) first.
    min_length: groups of linked nodes shorter in all are hidden."""
    item, data = _graph(item_id)
    hits = library.graph_hits(item)
    reference = None
    if hits and hits["queries"]:
        try:
            ref = _item(hits["queries"][0]["item"])
            reference = {**record_to_editor(library.records(ref)[0]), "item": ref["id"], "name": ref["label"]}
        except HTTPException:
            hits = None  # the reference is gone from the library
    only = {h["node"] for h in hits["hits"]} if hits and only_hits else None
    chosen = [graph.parse_walk(w) for w in walks.split(";") if w]  # the routes the user chose ("A+,B-;C+")
    found, left = graph.tracks(data, min_length, min_depth, only, chosen)
    return {**graph.track_view(data, found, hits, reference), "left": left, "nodes": len(data["nodes"]),
            "min_length": min_length}


class GraphNodesRequest(BaseModel):
    steps: list[str]  # the selected nodes, "12+", "7-": each as drawn, in drawn order
    name: str
    join: bool = False  # one sequence through them (they must follow each other), else one each


@router.post("/api/graph/{item_id}/nodes")
def save_graph_nodes(item_id: str, req: GraphNodesRequest):
    """Selected nodes as new sequences: one each (a list for several), or joined into one."""
    item, data = _graph(item_id)
    steps = [(s[:-1], s[-1]) for s in req.steps if len(s) > 1 and s[-1] in "+-"]
    if not steps:
        raise HTTPException(400, "No nodes selected")
    nodes = data["nodes"]
    try:
        if req.join:
            track = graph.joined(data, steps)
            seq = track["sequence"]
            record = SeqRecord(Seq(seq), description=f"nodes {', '.join(req.steps)} of {item['name']}"[:900])
            record.annotations.update(molecule_type="DNA", topology="circular" if track["circular"] else "linear")
            for p in track["nodes"] if len(track["nodes"]) > 1 else []:
                if p["start"] < len(seq):
                    record.features.append(SeqFeature(
                        FeatureLocation(p["start"], min(p["end"] + 1, len(seq)), strand=1 if p["o"] == "+" else -1),
                        type="misc_feature", qualifiers={"label": [f"node {p['id']}"], "note": [
                            f"assembly graph node {p['id']}, {nodes[p['id']]['length']:,} bp"
                            + (f", depth {nodes[p['id']]['depth']:.1f}x" if nodes[p["id"]]["depth"] else "")]}))
            records = [record]
        else:
            records = []
            for n, _ in steps:
                node = nodes[n]
                record = SeqRecord(Seq(graph.oriented(data, n, "+")), id=f"node_{n}", name=f"node_{n}",
                                   description=f"node {n} of {item['name']}, {node['length']:,} bp"
                                               + (f", depth {node['depth']:.1f}x" if node["depth"] else ""))
                record.annotations.update(molecule_type="DNA", topology="linear")
                records.append(record)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Couldn't join them: {e}. Select a stretch along one line, or export one sequence "
                                 "per node." if req.join else f"Couldn't make the sequences: {e}")
    with library._writing:
        new = library.add_list(records, req.name) if len(records) > 1 else library.add_sequence(records[0], req.name)
        library.same_folder(new, item)
        _record_created([new])
    return {"id": new, "count": len(records)}


class ContigGraphRequest(ResultDestination):
    ids: list[str]
    name: str
    min_overlap: int = 30


@router.post("/api/graph/from-contigs")
def graph_from_contigs(req: ContigGraphRequest):
    """An assembly graph from contigs (a list, or sequences): links where their ends overlap."""
    items = _sequences(req.ids)
    if len(items) < 1:
        raise HTTPException(400, "Select a list of contigs")
    _result_folder(req, items)  # reject ambiguous destinations before building the graph
    contigs, seen = [], set()
    for item in items:
        record = library.records(item)[0]
        name = re.sub(r"\.(fa|fasta|fna)$", "", record.name)
        while name in seen:
            name += "_2"
        seen.add(name)
        found = re.search(r"multi=([\d.]+)", record.description or "")
        contigs.append((name, str(record.seq), float(found.group(1)) if found else None))
    text, stats = graph.contigs_to_gfa(contigs, max(10, req.min_overlap))
    with library._writing:
        destination = _result_folder(req, items)
        new = library.add_graph(text, req.name or "contig graph")
        library.move([new], destination)
        _record_created([new])
    return {"id": new, **stats}


@router.post("/api/graph/{item_id}/clear-comparison")
def clear_graph_comparison(item_id: str):
    """Removes the reference comparison (the coloured matches and the key) from a graph."""
    item, _ = _graph(item_id)
    _free(item_id)
    library.clear_graph_hits(item)
    return {}


@router.get("/api/graph/{item_id}/layout")
def graph_layout(item_id: str, min_length: int = 0, min_depth: float = 0, only_hits: bool = False, to_scale: bool = False,
                 order: str = "track"):
    """The graph view's drawing (graph.layout): each group of linked nodes as
    parallel lines, the reference compared with it (if any) as marks on its nodes.
    Everything shown unless asked: min_length hides groups shorter in all, min_depth nodes.
    order: which group comes first (graph.GROUP_ORDERS)."""
    item, data = _graph(item_id)
    hits = library.graph_hits(item)
    reference = None
    if hits and hits["queries"]:
        try:
            ref = _item(hits["queries"][0]["item"])
            reference = {"item": ref["id"], "name": ref["label"], "length": hits["queries"][0]["length"]}
        except HTTPException:
            hits = None  # the reference is gone from the library
    only = {h["node"] for h in hits["hits"]} if hits and only_hits else None
    return {**graph.layout(data, min_length, min_depth, only, hits, to_scale, order), "reference": reference,
            "nodes": len(data["nodes"]), "min_length": min_length}


class GraphRouteRequest(BaseModel):
    walk: str                  # a main track's route, "LSC+,IR+,SSC+,IR-"
    turn: int | None = None    # a node of it to read on its other strand
    branch: str | None = None  # or a branch to put in it


@router.post("/api/graph/{item_id}/route")
def graph_route(item_id: str, req: GraphRouteRequest):
    """A main track's route changed (graph.turn_round, take_branch); the viewer
    remembers it and asks for the tracks with it."""
    _, data = _graph(item_id)
    steps = graph.parse_walk(req.walk)
    try:
        if req.turn is not None:
            steps = graph.turn_round(data, steps, req.turn)
        elif req.branch:
            steps = graph.take_branch(data, steps, graph.parse_walk(req.branch))
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))
    return {"walk": graph.walk_text(steps)}
