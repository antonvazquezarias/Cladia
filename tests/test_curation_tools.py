"""Distance trees, tip removal, the tRNA table, checked alignment rows, trimming."""
import random
from io import StringIO

import pytest
from Bio import Phylo

from cladia import align_io, distance_tree as dt, library
from cladia.tool_plugins import trnascan, trimal


def _mutate(seq, n, rng):
    seq = list(seq)
    for _ in range(n):
        seq[rng.randrange(len(seq))] = rng.choice("ACGT")
    return "".join(seq)


@pytest.fixture
def rows():
    rng = random.Random(2)
    root = "".join(rng.choice("ACGT") for _ in range(2000))
    a, b = _mutate(root, 80, rng), _mutate(root, 80, rng)
    return {"a1": _mutate(a, 20, rng), "a2": _mutate(a, 20, rng), "b1": _mutate(b, 20, rng),
            "b2": _mutate(b, 20, rng), "out": _mutate(root, 300, rng)}


def _clades(newick):
    tree = Phylo.read(StringIO(newick), "newick")
    return {frozenset(t.name for t in c.get_terminals()) for c in tree.get_nonterminals()}


@pytest.mark.parametrize("method", ["nj", "upgma"])
def test_distance_tree_groups_and_bootstrap(rows, method):
    newick, notes = dt.build(list(rows), list(rows.values()), method=method, bootstrap=50)
    clades = _clades(newick)
    assert frozenset({"a1", "a2"}) in clades and frozenset({"b1", "b2"}) in clades
    assert "50 bootstrap replicates" in notes[-1]
    supports = [c.confidence for c in Phylo.read(StringIO(newick), "newick").get_nonterminals() if c.confidence is not None]
    assert supports and max(supports) == 100


def test_nj_outgroup_roots_between_outgroup_and_rest(rows):
    newick, _ = dt.build(list(rows), list(rows.values()), outgroup="out")
    tree = Phylo.read(StringIO(newick), "newick")
    assert sorted(len(c.get_terminals()) for c in tree.root.clades) == [1, 4]
    assert [t.name for c in tree.root.clades for t in c.get_terminals() if len(c.get_terminals()) == 1] == ["out"]


def test_distance_tree_handles_gaps_and_refuses_bad_input(rows):
    gapped = dict(rows, a1="-" * 100 + rows["a1"][100:])
    dt.build(list(gapped), list(gapped.values()), bootstrap=0)
    with pytest.raises(ValueError, match="at least three"):
        dt.build(["a", "b"], ["ACGT", "ACGT"])
    with pytest.raises(ValueError, match="share no compared sites"):
        dt.build(["a", "b", "c"], ["AC--", "--GT", "ACGT"])
    with pytest.raises(ValueError, match="proteins"):
        dt.build(list(rows), list(rows.values()), distance="poisson")


def test_prune_tree_keeps_supports_and_refuses_too_few(tmp_path, monkeypatch):
    path = tmp_path / "t.nwk"
    path.write_text("(((a:1,b:1)90:1,c:2)80:1,(d:1,e:1)95:1);", encoding="utf-8")
    monkeypatch.setattr(library.history, "checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(library.history, "protect", lambda *a, **k: __import__("contextlib").nullcontext())
    item = {"id": "x", "path": str(path)}
    assert library.prune_tree(item, ["b"]) == 1
    tree = Phylo.read(StringIO(path.read_text()), "newick")
    assert sorted(t.name for t in tree.get_terminals()) == ["a", "c", "d", "e"]
    assert library.prune_tree(item, ["a"]) == 1
    with pytest.raises(ValueError, match="at least three"):
        library.prune_tree(item, ["c"])
    with pytest.raises(ValueError, match="Select the tips"):
        library.prune_tree(item, ["nope"])


TABLE = """Sequence\t\ttRNA\tBounds\ttRNA\tAnti\tIntron Bounds\tInf
Name\ttRNA #\tBegin\tEnd\tType\tCodon\tBegin\tEnd\tScore\tNote
--------\t------\t-----\t------\t----\t-----\t-----\t----\t------\t------
s0\t1\t247\t333\tLeu\tCAG\t0\t0\t84.5\t
s0\t2\t900\t801\tTrp\tCCA\t860\t840\t60.1\tpseudo
"""


def test_trnascan_table_and_features():
    hits = trnascan.read_table(TABLE)["s0"]
    assert [h["type"] for h in hits] == ["Leu", "Trp"] and hits[1]["intron"] == (860, 840)
    gene, trna = trnascan.features(hits[0], 2000)
    assert (gene["start"], gene["end"], gene["strand"]) == (246, 332, 1)
    assert trna["notes"]["product"] == ["tRNA-Leu"] and trna["notes"]["gene"] == ["trnL-CAG"]
    gene, trna = trnascan.features(hits[1], 2000)  # minus strand with an intron: two parts
    assert trna["strand"] == -1 and trna["locations"] == [{"start": 800, "end": 838}, {"start": 860, "end": 899}]
    wrapped = dict(hits[0], begin=1990, end=2070)  # across the origin of a 2,000 bp circle
    assert trnascan.features(wrapped, 2000)[1]["locations"] == [{"start": 1989, "end": 1999}, {"start": 0, "end": 69}]


def test_distance_tree_engine_records_its_citations(tmp_path, rows, monkeypatch):
    from cladia.tool_plugins import distance_tree as engine
    import cladia.engine_api as api
    added = {}
    monkeypatch.setattr(api, "add_output", lambda job, path, name, kind=None, extra=None, source=None: added.update(name=name))
    job = {"log": "", "cancel": False}
    api.steps = lambda job, *names: job.update(progress={"steps": list(names), "at": 0, "fraction": None, "detail": ""})
    monkeypatch.setattr(api, "steps", api.steps)
    records = [type("R", (), {"id": k, "seq": v})() for k, v in rows.items()]
    monkeypatch.setattr(api, "tree_rows", lambda item, pattern=None: (records, {}))
    engine.run(job, tmp_path, "tree.build", [{}], "t", method="upgma", distance="jc", bootstrap=5)
    cited = {c["tool"] for c in job["_run_details"]["citations"]}
    assert {"UPGMA", "Jukes-Cantor distance", "Bootstrap"} <= cited and added["name"] == "t"


def test_alignment_back_checks_rows(tmp_path):
    rows = [("seq000001", "a", "i1"), ("seq000002", "b", "i2")]
    good = tmp_path / "good.fa"
    good.write_text(">seq000002\nAC-GT\n>seq000001\nACCGT\n")
    out = align_io.back(good, rows, {"seq000001": "ACCGT", "seq000002": "ACGT"}, "Tool")
    assert [(r.id, str(r.seq)) for r in out] == [("a", "ACCGT"), ("b", "AC-GT")]  # input order, own names
    assert align_io.sources(rows) == {"a": {"item": "i1"}, "b": {"item": "i2"}}
    changed = tmp_path / "changed.fa"
    changed.write_text(">seq000001\nACCGA\n>seq000002\nAC-GT\n")
    with pytest.raises(RuntimeError, match="changed the residues of a"):
        align_io.back(changed, rows, {"seq000001": "ACCGT", "seq000002": "ACGT"}, "Tool")
    missing = tmp_path / "missing.fa"
    missing.write_text(">seq000001\nACCGT\n")
    with pytest.raises(RuntimeError, match="incomplete"):
        align_io.back(missing, rows, None, "Tool")


def test_new_engines_declare_what_the_registry_needs():
    from cladia.tool_registry import build_registry
    registry = build_registry()
    for engine_id in ("muscle", "macse", "raxml_ng", "mrbayes", "trnascan", "trimal", "clipkit"):
        engine = registry.engines[engine_id]
        assert engine["optional"] and callable(engine["install"]) and callable(engine["uninstall"])
        assert engine["conda"]["spec"]
    assert registry.engines["distance_tree"].get("optional") is None
    assert trimal.METHODS["gappyout"]


ARAGORN_BATCH = """>s0
3 genes found
1   tRNA-Leu                [247,333]	35  	(cag)
2   tRNA-Trp               c[800,899]	34  	(cca)i(39,21)
3   tmRNA                   [1001,1363]	90,125	ANDNFAAEDNVALAA*
>s1
1 gene found
1   tRNA-Leu                [533,40]	35  	(cag)
>end 	2 sequences 4 genes
"""


def test_aragorn_batch_output_and_features():
    from cladia.tool_plugins import aragorn
    hits = aragorn.read_batch(ARAGORN_BATCH)
    assert [h["kind"] for h in hits["s0"]] == ["tRNA", "tRNA", "tmRNA"] and len(hits["s1"]) == 1
    gene, trna = aragorn.features(hits["s0"][0], 2000)
    assert (gene["start"], gene["end"], gene["strand"]) == (246, 332, 1)
    assert trna["notes"]["gene"] == ["trnL-CAG"] and trna["notes"]["product"] == ["tRNA-Leu"]
    gene, trna = aragorn.features(hits["s0"][1], 2000)  # minus strand, 21 bp intron 39 bases from the 5' end
    assert trna["strand"] == -1 and trna["locations"] == [{"start": 799, "end": 839}, {"start": 861, "end": 898}]
    assert trna["notes"]["gene"] == ["trnW-CCA"]
    gene, tm = aragorn.features(hits["s0"][2], 2000)
    assert tm["type"] == "tmRNA" and gene["notes"]["gene"] == ["ssrA"] and "ANDNFAAEDNVALAA" in tm["notes"]["note"][0]
    gene, trna = aragorn.features(hits["s1"][0], 579)  # across the origin of a circle
    assert trna["locations"] == [{"start": 532, "end": 578}, {"start": 0, "end": 39}]
