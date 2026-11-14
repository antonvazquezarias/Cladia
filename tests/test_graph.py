"""Assembly graphs (GFA): reading, the library item, paths and nodes saved as sequences.

graph_small.gfa: the shape of a real organelle assembly's graph (65 nodes with read counts,
4 links of 115 bp), its sequences random; graph_spades.gfa: one 9-node group of a SPAdes
graph, likewise (tests/data/README.md).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cladia import config, graph, library
from cladia.genbank import read_records

DATA = Path(__file__).parent / "data"

# a toy graph: A+ -> B+ -> A+ around a circle (overlaps 3), C linked to nothing
TOY = """H\tVN:Z:1.0
S\tA\tAAAACCCGGG\tDP:f:10
S\tB\tGGGTTTTAAA\tKC:i:50
S\tC\t*\tLN:i:7
L\tA\t+\tB\t+\t3M
L\tB\t+\tA\t+\t3M
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    from cladia.server import app
    return TestClient(app)


def test_read_gfa():
    g = graph.read_gfa(TOY)
    assert g["nodes"]["A"]["depth"] == 10 and g["nodes"]["B"]["depth"] == 5  # KC per base
    assert g["nodes"]["C"] == {"seq": "", "length": 7, "depth": None}
    assert g["links"] == [("A", "+", "B", "+", 3), ("B", "+", "A", "+", 3)]
    real = graph.read_gfa((DATA / "graph_small.gfa").read_text())
    assert len(real["nodes"]) == 65 and len(real["links"]) == 4
    node = real["nodes"]["5222283"]
    assert node["length"] == 46699 and round(node["depth"], 2) == round(207147 / 46699, 2)


def test_paths():
    g = graph.read_gfa(TOY)
    assert graph.path_sequence(g, [("A", "+"), ("B", "+")]) == ("AAAACCCGGGTTTTAAA"[:-3], True)  # closes: A follows B
    # the other strand of the same path
    seq, circular = graph.path_sequence(g, [("B", "-"), ("A", "-")])
    assert circular and seq == graph.revcomp("AAAACCCGGGTTTTAAA")[:-3]
    with pytest.raises(ValueError, match="no link"):
        graph.path_sequence(g, [("A", "+"), ("B", "-")])
    with pytest.raises(ValueError, match="no sequence"):
        graph.path_sequence(g, [("C", "+")])
    real = graph.read_gfa((DATA / "graph_small.gfa").read_text())
    seq, circular = graph.path_sequence(real, [("5206472", "+"), ("5207338", "-"), ("5236788", "-")])
    lengths = [real["nodes"][n]["length"] for n in ("5206472", "5207338", "5236788")]
    assert len(seq) == sum(lengths) - 2 * 115 and not circular


def test_graph_item(client, tmp_path):
    source = tmp_path / "assembly.gfa"
    source.write_text(TOY, newline="")
    r = client.post("/api/library/add", json={"paths": [str(DATA / "graph_small.gfa"), str(source)]}).json()
    assert r["errors"] == []
    found = {i["label"]: i for i in client.get("/api/library").json()}
    assert found["graph_small"]["kind"] == found["assembly"]["kind"] == "graph"
    toy = found["assembly"]["id"]
    view = client.get(f"/api/graph/{toy}").json()
    assert [n["id"] for n in view["nodes"]] == ["A", "B", "C"] and view["nodes"][0]["gc"] == 60.0
    assert client.get(f"/api/info/{found['graph_small']['id']}").json()["nodes"] == 65
    # as tracks: A and B close into a circle; C (no sequence) is left out
    shown = client.get(f"/api/graph/{toy}/tracks").json()
    assert [g["name"] for g in shown["genomes"]] == ["Track 1 (circular)"] and shown["left"] == 1
    track = shown["genomes"][0]["data"]
    assert track["circular"] and track["sequence"] == "AAAACCCGGGTTTT"
    assert [(f["name"], f["start"], f["end"], f["strand"]) for f in track["features"]] == [
        ("A+ 10.0×", 0, 9, 1), ("B+ 5.0×", 7, 13, 1)]  # B's end runs into A's start (the closing overlap)
    # Export: the GFA itself, or its nodes as FASTA
    gfa = client.post("/api/export", json={"ids": [toy], "format": "gfa"}).json()
    fasta = client.post("/api/export", json={"ids": [toy], "format": "fasta"}).json()
    assert gfa["text"] == TOY and fasta["text"].startswith(">node_A length=10 depth=10.0\nAAAACCCGGG")


def test_graph_hits(client, tmp_path):
    source = tmp_path / "assembly.gfa"
    source.write_text(TOY, newline="")
    graph_id = client.post("/api/library/add", json={"paths": [str(source)]}).json()["ids"][0]
    assert client.get(f"/api/graph/{graph_id}").json()["hits"] is None
    result = {"queries": [{"name": "ref", "item": "x", "length": 20}],
              "hits": [{"query": 0, "node": "A", "identity": 99.0, "qstart": 1, "qend": 10, "start": 1, "end": 10}]}
    library.set_graph_hits(library.get_item(graph_id), result)
    assert client.get(f"/api/graph/{graph_id}").json()["hits"] == result
    # kept beside the graph: a copy has them too, and they go with it to the trash
    copy = library.duplicate(library.get_item(graph_id))
    assert library.graph_hits(library.get_item(copy)) == result
    assert client.post(f"/api/graph/{graph_id}/blast", json={"queries": [graph_id]}).status_code == 400


def test_tracks():
    """A plastid-like graph: LSC, SSC and the inverted repeat collapsed into one node
    at twice their depth; a bubble (two versions of a stretch) off the LSC."""
    import random
    rnd = random.Random(3)
    seq = lambda n: "".join(rnd.choice("ACGT") for _ in range(n))
    lsc, ir, ssc, x, y = seq(800), seq(300), seq(200), seq(50), seq(50)
    gfa = "\n".join([f"S\tLSC\t{lsc}\tDP:f:100", f"S\tIR\t{ir}\tDP:f:205", f"S\tSSC\t{ssc}\tDP:f:98",
                     f"S\tX\t{x}\tDP:f:50", f"S\tY\t{y}\tDP:f:48", f"S\tlone\t{seq(400)}\tDP:f:3",
                     "L\tLSC\t+\tIR\t+\t0M", "L\tIR\t+\tSSC\t+\t0M", "L\tSSC\t+\tIR\t-\t0M", "L\tIR\t-\tLSC\t+\t0M",
                     "L\tLSC\t+\tX\t+\t0M", "L\tLSC\t+\tY\t+\t0M"])
    g = graph.read_gfa(gfa)
    assert graph.copies(g, ["LSC", "IR", "SSC"]) == {"LSC": 1, "IR": 2, "SSC": 1}
    found, left = graph.tracks(g)
    main = found[0]
    assert main["circular"] and [p["id"] + p["o"] for p in main["nodes"]] == ["LSC+", "IR+", "SSC+", "IR-"]
    assert main["sequence"] == lsc + ir + ssc + graph.revcomp(ir)
    # the bubble's two sides: branches under the LSC's end
    branches = [t for t in found if t["branch_of"] == 0]
    assert sorted(t["nodes"][0]["id"] for t in branches) == ["X", "Y"] and {t["shift"] for t in branches} == {800}
    assert found[-1]["name"] == "node lone" and not found[-1]["circular"] and left == 0
    # hidden: shallow nodes; only the groups with some nodes
    assert [t["name"] for t in graph.tracks(g, min_depth=10)[0]][-1] != "node lone"
    assert [t["name"] for t in graph.tracks(g, only={"lone"})[0]] == ["node lone"]
    # a reference's hits on IR land on both of its copies, the second reversed
    hits = {"queries": [{"name": "ref", "item": "r", "length": 300}],
            "hits": [{"query": 0, "node": "IR", "identity": 99.0, "qstart": 1, "qend": 300, "start": 1, "end": 300, "bits": 500}]}
    view = graph.track_view(g, found, hits, {"name": "ref", "sequence": ir, "features": []})
    parts = [b["parts"][1] for b in view["blocks"]]
    assert [(p["start"], p["end"], p["strand"]) for p in parts] == [(801, 1100, 1), (1301, 1600, -1)]


def test_routes():
    """The SSC turned round (the other plastid isoform); a bubble's other side put in."""
    gfa = "\n".join(["S\tLSC\tAAAACCCC\tDP:f:100", "S\tIR\tGGGG\tDP:f:200", "S\tSSC\tTTTTA\tDP:f:100",
                     "L\tLSC\t+\tIR\t+\t0M", "L\tIR\t+\tSSC\t+\t0M", "L\tSSC\t+\tIR\t-\t0M", "L\tIR\t-\tLSC\t+\t0M"])
    g = graph.read_gfa(gfa)
    walk = graph.parse_walk("LSC+,IR+,SSC+,IR-")
    turned = graph.turn_round(g, walk, 2)
    assert graph.walk_text(turned) == "LSC+,IR+,SSC-,IR-"
    with pytest.raises(ValueError):
        graph.turn_round(g, walk, 1)  # the IR turned round: nothing links the LSC to its other strand
    found, _ = graph.tracks(g, walks=[turned])
    assert found[0]["walk"] == "LSC+,IR+,SSC-,IR-" and found[0]["circular"] and found[0]["chosen"]
    # a bubble A -> (X | Y) -> B: Y put in instead of X; X becomes the branch
    bubble = graph.read_gfa("\n".join(["S\tA\tAAAAAAAAAA", "S\tX\tCCCCC", "S\tY\tGGGG", "S\tB\tTTTTTTTT",
                                       "L\tA\t+\tX\t+\t0M", "L\tX\t+\tB\t+\t0M", "L\tA\t+\tY\t+\t0M", "L\tY\t+\tB\t+\t0M"]))
    main = graph.tracks(bubble)[0][0]
    assert main["walk"] == "A+,X+,B+"
    swapped = graph.take_branch(bubble, graph.parse_walk(main["walk"]), [("Y", "+")])
    found, _ = graph.tracks(bubble, walks=[swapped])
    assert found[0]["walk"] == "A+,Y+,B+" and [t["walk"] for t in found[1:]] == ["X+"]


def test_lanes_and_connectors():
    """A bubble's other side in a lane under the track (the longest walk takes the
    dead end D); links the rows don't show (out to the branch, back in) become connectors."""
    gfa = "\n".join(["S\tA\t" + "A" * 400, "S\tX\t" + "C" * 50, "S\tY\t" + "G" * 60, "S\tB\t" + "T" * 400,
                     "S\tD\t" + "AC" * 30,
                     "L\tA\t+\tX\t+\t0M", "L\tX\t+\tB\t+\t0M", "L\tA\t+\tY\t+\t0M", "L\tY\t+\tB\t+\t0M",
                     "L\tB\t+\tD\t+\t0M"])
    g = graph.read_gfa(gfa)
    found, _ = graph.tracks(g)
    view = graph.track_view(g, found)
    rows = view["genomes"]
    assert [r["name"] for r in rows] == ["Track 1", "Track 1 branches"]
    main, lane = rows
    assert main["walk"] == "A+,Y+,B+,D+" and [b["walk"] for b in lane["lane"]] == ["X+"]
    assert lane["data"]["sequence"].startswith("-" * 400 + "C" * 50) and lane["branch_of"] == 0
    # A's end -> X's start, X's end -> B's start (A->Y->B->D are on the row)
    assert sorted((c["from"], c["to"]) for c in view["connectors"]) == sorted([
        ([0, 0, "end"], [1, 0, "start"]), ([1, 0, "end"], [0, 2, "start"])])


def test_circle_through_a_short_join():
    """A long contig closing on itself through either of two short nodes (as
    1175689 in the user's SPAdes graph): circular through one, the other a
    branch; and the short joins aren't hidden by the length filter."""
    gfa = "\n".join(["S\tBIG\t" + "ACGT" * 2000 + "\tDP:f:70", "S\tj1\t" + "A" * 175 + "\tDP:f:32",
                     "S\tj2\t" + "C" * 151 + "\tDP:f:29", "S\tjunk\t" + "G" * 100,
                     "L\tj1\t-\tBIG\t-\t0M", "L\tBIG\t-\tj1\t-\t0M", "L\tj2\t+\tBIG\t-\t0M", "L\tBIG\t-\tj2\t+\t0M"])
    g = graph.read_gfa(gfa)
    for min_length in (0, 500):
        found, left = graph.tracks(g, min_length=min_length)
        main = found[0]
        assert main["circular"] and len(main["nodes"]) == 2 and main["nodes"][0]["id"] == "BIG"
        assert [t["nodes"][0]["id"] for t in found if t["branch_of"] == 0] == [{"j1", "j2"}.difference(
            {main["nodes"][1]["id"]}).pop()]
    assert left == 1  # the short node on its own ("junk") is the one hidden at 500


def test_layout():
    """The graph view's parallel layout: the longest path as the main line (lane 0),
    a bubble's other side beside it, a ring for a contig closing through a join,
    an inverted repeat's links as twisted ones, short tangles hidden."""
    bubble = graph.read_gfa("\n".join(["S\tA\t" + "A" * 4000, "S\tX\t" + "C" * 500, "S\tY\t" + "G" * 900,
                                       "S\tB\t" + "T" * 3000, "S\tt1\tACGT", "S\tt2\tACGT",
                                       "L\tA\t+\tX\t+\t0M", "L\tX\t+\tB\t+\t0M", "L\tA\t+\tY\t+\t0M",
                                       "L\tY\t+\tB\t+\t0M", "L\tt1\t+\tt2\t+\t0M"]))
    view = graph.layout(bubble, min_length=100)
    assert len(view["groups"]) == 1 and view["left"] == 2  # the 4 bp tangle t1-t2 hidden
    g = view["groups"][0]
    lanes = {n: p["lane"] for n, p in g["nodes"].items()}
    assert lanes["A"] == lanes["Y"] == lanes["B"] == 0 and lanes["X"] != 0  # Y is the longer side: on the main line
    assert g["nodes"]["X"]["x0"] == g["nodes"]["Y"]["x0"]  # both sides leave A together
    assert not g["circular"] and {tuple(l[:4]) for l in g["links"]} >= {("A", "end", "X", "start"), ("X", "end", "B", "start")}
    # a long contig closing through either of two short joins: a ring
    ring = graph.read_gfa("\n".join(["S\tBIG\t" + "ACGT" * 2000, "S\tj1\t" + "A" * 175, "S\tj2\t" + "C" * 151,
                                     "L\tj1\t-\tBIG\t-\t0M", "L\tBIG\t-\tj1\t-\t0M", "L\tj2\t+\tBIG\t-\t0M", "L\tBIG\t-\tj2\t+\t0M"]))
    g = graph.layout(ring, min_length=500)["groups"][0]
    assert g["circular"] and set(g["nodes"]) == {"BIG", "j1", "j2"}  # the joins kept: they bridge BIG to itself
    # a plastid: LSC IR SSC on the main line, the IR's second copy as two twisted links
    plastid = graph.read_gfa("\n".join(["S\tLSC\t" + "A" * 8000, "S\tIR\t" + "C" * 2500, "S\tSSC\t" + "G" * 1800,
                                        "L\tLSC\t+\tIR\t+\t0M", "L\tIR\t+\tSSC\t+\t0M", "L\tSSC\t+\tIR\t-\t0M", "L\tIR\t-\tLSC\t+\t0M"]))
    g = graph.layout(plastid)["groups"][0]
    assert g["main"] == ["LSC", "IR", "SSC"] and sorted(l[4] for l in g["links"]) == ["forward", "forward", "twisted", "twisted"]


def test_selected_nodes(client, tmp_path, monkeypatch):
    """Export of the selected nodes (one each, or joined along a path) and BLAST of a node."""
    source = tmp_path / "assembly.gfa"
    source.write_text(TOY, newline="")
    graph_id = client.post("/api/library/add", json={"paths": [str(source)]}).json()["ids"][0]
    made = client.post(f"/api/graph/{graph_id}/nodes", json={"steps": ["A+", "B+"], "name": "ab"}).json()
    assert made["count"] == 2
    labels = {i["label"] for i in client.get("/api/library").json()}
    assert {"ab › node_A", "ab › node_B"} <= labels
    one = client.post(f"/api/graph/{graph_id}/nodes", json={"steps": ["A+"], "name": "just A"}).json()
    assert one["count"] == 1
    # joined: A then B, closing on A (the overlap 3 once): a circular 14 bp sequence with a feature per node
    joined = client.post(f"/api/graph/{graph_id}/nodes", json={"steps": ["A+", "B+"], "name": "ab joined", "join": True}).json()
    data = client.get(f"/api/sequences/{joined['id']}").json()
    assert data["sequence"] == "AAAACCCGGGTTTT" and data["circular"]
    assert [(f["name"], f["start"], f["end"]) for f in data["features"]] == [("node A", 0, 9), ("node B", 7, 13)]
    # nodes that don't follow each other can't be joined
    bad = client.post(f"/api/graph/{graph_id}/nodes", json={"steps": ["A+", "B-"], "name": "x", "join": True})
    assert bad.status_code == 400 and "Couldn't join them" in bad.json()["detail"]
    # BLAST: a node is a query (its bases come from the graph)
    from cladia import jobs
    sent = []
    monkeypatch.setattr(jobs, "submit", lambda *a, **k: sent.append(k) or {"id": "j"})
    r = client.post("/api/tool-registry/run", json={"operation": "blast.search", "engine": "ncbi_blast",
        "inputs": {"queries": [{"item": graph_id, "node": "A"}]}, "options": {"program": "megablast", "database": "core_nt"}})
    assert r.status_code == 200 and sent[0]["query"]["sequence"] == "AAAACCCGGG" and sent[0]["query"]["name"] == "assembly node A"
    assert client.post("/api/tool-registry/run", json={"operation": "blast.search", "engine": "ncbi_blast",
        "inputs": {"queries": [{"item": graph_id, "node": "nope"}]}}).status_code == 400


def test_layout_keeps_sequence_free_bridge():
    g = graph.read_gfa("S\tA\tAAAA\nS\tB\t*\tLN:i:4\nS\tC\tCCCC\n"
                       "L\tA\t+\tB\t+\t0M\nL\tB\t+\tC\t+\t0M")
    view = graph.layout(g)
    assert len(view["groups"]) == 1
    assert set(view["groups"][0]["nodes"]) == {"A", "B", "C"}
    assert view["left"] == 0
    tracks, left = graph.tracks(g)
    assert left == 1 and len(tracks) == 2


def test_ring_reserves_space_for_closing_connector():
    g = graph.read_gfa("S\tA\tAAAA\nS\tB\tCCCC\nL\tA\t+\tB\t+\t0M\nL\tB\t+\tA\t+\t0M")
    group = graph.layout(g)["groups"][0]
    assert group["circular"]
    assert group["span"] > max(n["x1"] for n in group["nodes"].values())


def test_layout_many_parallel_branches():
    lines = ["S\tA\t" + "A" * 100, "S\tZ\t" + "T" * 100]
    for i in range(800):
        lines += [f"S\tB{i}\tCCCC", f"L\tA\t+\tB{i}\t+\t0M", f"L\tB{i}\t+\tZ\t+\t0M"]
    group = graph.layout(graph.read_gfa("\n".join(lines)))["groups"][0]
    assert len(group["nodes"]) == 802
    assert len(group["links"]) == 1600


def test_ring_local_cycle_stays_beside_its_attachment():
    g = graph.read_gfa((DATA / "graph_spades.gfa").read_text())
    group = graph.layout(g, only={"1106137"})["groups"][0]
    branch, anchor = (group["nodes"][n] for n in ("1106137", "1187540"))
    assert branch["lane"] > 0  # outside the ring
    assert abs((branch["x0"] + branch["x1"]) / 2 - (anchor["x0"] + anchor["x1"]) / 2) < 0.1
    assert len(group["links"]) == 8
    # The branch which really closes across the origin stays there.
    closing = group["nodes"]["162071"]
    assert closing["x0"] > branch["x1"]


def test_local_return_arc_flips_all_endpoints():
    g = graph.read_gfa((DATA / "graph_spades.gfa").read_text())
    group = graph.layout(g, only={"1106137"})["groups"][0]
    assert group["nodes"]["1106137"]["o"] == "-"
    attached = [l for l in group["links"] if "1106137" in (l[0], l[2])]
    assert len(attached) == 2
    assert all(l[1] == l[3] for l in attached)
    # The displayed endpoints still represent exactly the original GFA links.
    table = graph._link_table(g)
    for a, sa, b, sb, _ in group["links"]:
        ao = group["nodes"][a]["o"]
        bo = group["nodes"][b]["o"]
        assert (a, ao if sa == "end" else graph.flip(ao),
                b, bo if sb == "start" else graph.flip(bo)) in table


def test_single_self_link_retains_a_connector_gap():
    for orientation in ("+", "-"):
        g = graph.read_gfa(f"S\tA\tACGT\nL\tA\t{orientation}\tA\t{orientation}\t0M")
        group = graph.layout(g)["groups"][0]
        assert group["circular"]
        assert group["nodes"]["A"]["x0"] == 0
        assert group["nodes"]["A"]["x1"] < group["span"]
        assert len(group["links"]) == 1


def test_alternative_closure_sits_beside_ring_gap():
    g = graph.read_gfa((DATA / "graph_spades.gfa").read_text())
    group = graph.layout(g, only={"1118900"})["groups"][0]
    nodes = group["nodes"]
    alternative, closure = nodes["1118900"], nodes["1091910"]
    assert alternative["lane"] > 0
    assert abs((alternative["x0"] + alternative["x1"]) / 2 -
               (closure["x0"] + closure["x1"]) / 2) < 0.1
    assert alternative["o"] == "-"
    # Both connectors join nearby ends at the origin, not across the ring.
    for a, sa, b, sb, _ in group["links"]:
        if "1118900" not in (a, b):
            continue
        x = nodes[a]["x1" if sa == "end" else "x0"]
        y = nodes[b]["x1" if sb == "end" else "x0"]
        distance = abs(x - y)
        assert min(distance, group["span"] - distance) < 15


@pytest.mark.parametrize("long_anchor", [False, True])
def test_ring_compaction_uses_topology_and_preserves_links(long_anchor):
    # The same rules handle a side loop and an alternative closing route,
    # independently of node names and record order.
    lengths = {"anchor": 8000 if long_anchor else 200, "join": 170,
               "side": 150, "rest": 500 if long_anchor else 8000}
    def make(names, reverse=False):
        lines = [f"S\t{names[n]}\t" + "A" * size for n, size in lengths.items()]
        lines += [f"L\t{names[a]}\t+\t{names[b]}\t+\t0M" for a, b in
                  [("anchor", "join"), ("join", "rest"), ("rest", "anchor"),
                   ("anchor", "side"), ("side", "anchor")]]
        return graph.read_gfa("\n".join(reversed(lines) if reverse else lines))
    names = {n: n for n in lengths}
    renamed = {n: f"different_{i}" for i, n in enumerate(lengths)}
    results = []
    for mapping, reverse in [(names, False), (renamed, True)]:
        g = make(mapping, reverse)
        group = graph.layout(g)["groups"][0]
        table = graph._link_table(g)
        for a, sa, b, sb, _ in group["links"]:
            ao, bo = group["nodes"][a]["o"], group["nodes"][b]["o"]
            assert (a, ao if sa == "end" else graph.flip(ao),
                    b, bo if sb == "start" else graph.flip(bo)) in table
        side = group["nodes"][mapping["side"]]
        results.append((side["x0"], side["x1"], side["lane"], side["o"]))
        anchor = group["nodes"][mapping["anchor"]]
        if long_anchor:
            assert side["x0"] >= anchor["x1"] - 10
        else:
            assert abs((side["x0"] + side["x1"]) / 2 - (anchor["x0"] + anchor["x1"]) / 2) < 10
    assert results[0] == results[1]


def test_viridis_depth_palette_matches_legend():
    assert graph.depth_color(0) == "#fde725"
    assert graph.depth_color(0.5) == "#21918c"
    assert graph.depth_color(1) == "#440154"
    assert graph.depth_color(-1) == graph.depth_color(0)
    assert graph.depth_color(2) == graph.depth_color(1)
    g = graph.read_gfa("S\tA\tAAAA\tDP:f:10\nS\tB\tCCCC\tDP:f:20")
    view = graph.layout(g)
    assert view["depth_palette"] == graph.DEPTH_PALETTE
    assert {n["color"] for group in view["groups"] for n in group["nodes"].values()} == {graph.depth_color(0), graph.depth_color(1)}


def test_linear_branches_reuse_lanes_instead_of_drifting():
    g = graph.read_gfa((DATA / "graph_spades.gfa").read_text())
    group = graph.layout(g, only={"1191540"})["groups"][0]
    assert not group["circular"] and len(group["nodes"]) == 26
    places = list(group["nodes"].values())
    right = max(p["x1"] for p in places)
    # Later branches return towards the main lane after the first tangle ends.
    assert max(abs(p["lane"]) for p in places if p["x0"] > right * 0.75) <= 2
    for level in {p["lane"] for p in places}:
        row = sorted((p for p in places if p["lane"] == level), key=lambda p: p["x0"])
        for a, b in zip(row, row[1:]):
            assert a["x1"] + graph.NODE_GAP <= b["x0"] + 0.11


def test_big_ring_layout_stays_quick():
    """A ring of many nodes skips the quadratic compaction (it took seconds): still laid out, still a ring."""
    import random
    import time
    rnd = random.Random(2)
    lines = [f"S\tm{i}\t" + "A" * rnd.randint(300, 3000) for i in range(450)]
    lines += [f"L\tm{i}\t+\tm{(i + 1) % 450}\t+\t0M" for i in range(450)]
    for j in range(150):
        a = rnd.randrange(450)
        lines += [f"S\tb{j}\t" + "C" * 200, f"L\tm{a}\t+\tb{j}\t+\t0M", f"L\tb{j}\t+\tm{(a + 2) % 450}\t+\t0M"]
    started = time.time()
    group = graph.layout(graph.read_gfa("\n".join(lines)))["groups"][0]
    assert group["circular"] and len(group["nodes"]) == 600 and time.time() - started < 2


def test_clear_comparison(client):
    gid = client.post("/api/library/upload", json={"filename": "t.gfa", "text": TOY}).json()["ids"][0]
    result = {"queries": [{"name": "ref", "item": "r", "length": 10}],
              "hits": [{"query": 0, "node": "A", "identity": 99.0, "qstart": 1, "qend": 10, "start": 1, "end": 10}]}
    library.set_graph_hits(library.get_item(gid), result)
    entry = lambda: next(e for e in library.load() if e["id"] == gid)
    path = library.library_dir() / entry()["hits"]
    assert path.exists()
    assert client.post(f"/api/graph/{gid}/clear-comparison").status_code == 200
    assert "hits" not in entry() and not path.exists()
    assert client.get(f"/api/graph/{gid}").json()["hits"] is None


def test_groups_are_ordered_by_longest_track_by_default():
    seq = lambda n: "ACGT" * (n // 4)
    # group 1: a plain chain (track 652); group 2: a bubble, more bases in all (1000) but a shorter track (600)
    gfa = "\n".join([
        "H\tVN:Z:1.0", f"S\tA\t{seq(300)}\tDP:f:10", f"S\tB\t{seq(352)}\tDP:f:10",
        f"S\tC\t{seq(100)}\tDP:f:10", f"S\tD1\t{seq(400)}\tDP:f:10", f"S\tD2\t{seq(400)}\tDP:f:10", f"S\tF\t{seq(100)}\tDP:f:10",
        "L\tA\t+\tB\t+\t0M", "L\tC\t+\tD1\t+\t0M", "L\tC\t+\tD2\t+\t0M", "L\tD1\t+\tF\t+\t0M", "L\tD2\t+\tF\t+\t0M"]) + "\n"
    g = graph.read_gfa(gfa)
    names = lambda view: [(grp["name"], sorted(grp["nodes"])[0]) for grp in view["groups"]]
    by_track = graph.layout(g)  # the default
    assert names(by_track) == [("Group 1", "A"), ("Group 2", "C")] and by_track["groups"][0]["track"] == 652
    assert by_track["groups"][1]["track"] == 600 and by_track["groups"][1]["bases"] == 1000
    assert names(graph.layout(g, order="total")) == [("Group 1", "C"), ("Group 2", "A")]
    assert names(graph.layout(g, order="nodes")) == [("Group 1", "C"), ("Group 2", "A")]  # 5 nodes, 2 nodes
    assert names(graph.layout(g, order="nonsense")) == names(by_track)
