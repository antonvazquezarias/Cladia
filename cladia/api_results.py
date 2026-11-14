"""Tree, comparison, figure and analysis result routes."""
from .request_context import _free, _item
import base64
import re
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel
from . import figures, library, ncbi
from . import protein_properties as protein_properties_module

from fastapi import APIRouter

router = APIRouter()

@router.get("/api/tree/{item_id}")
def get_tree(item_id: str):
    """A tree item: its nodes for the tree viewer (library.tree_nodes), and its Newick text."""
    item = _item(item_id)
    if item["kind"] != "tree":
        raise HTTPException(400, f"{item['label']} is not a tree")
    return {"nodes": library.tree_nodes(item), "newick": Path(item["path"]).read_text(encoding="utf-8").strip()}


class RerootRequest(BaseModel):
    outgroup: list[str]  # the tips of the clade the viewer rerooted on


@router.post("/api/tree/{item_id}/reroot")
def reroot_tree(item_id: str, req: RerootRequest):
    """Saves a tree rooted where the viewer rerooted it."""
    _free(item_id)
    item = _item(item_id)
    if item["kind"] != "tree":
        raise HTTPException(400, f"{item['label']} is not a tree")
    try:
        previous = library.reroot_tree(item, req.outgroup)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(507, f"Couldn't save tree: {e}")
    return {"id": item_id, "previous": previous}  # the tree as it was: the viewer's Ctrl+Z


class PruneRequest(BaseModel):
    tips: list[str]


@router.post("/api/tree/{item_id}/prune")
def prune_tree(item_id: str, req: PruneRequest):
    """Saves a tree without the given tips (↶ goes back through its saved versions)."""
    _free(item_id)
    item = _item(item_id)
    if item["kind"] != "tree":
        raise HTTPException(400, f"{item['label']} is not a tree")
    try:
        return {"removed": library.prune_tree(item, req.tips)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(507, f"Couldn't save tree: {e}")


class TreeTextRequest(BaseModel):
    text: str


@router.put("/api/tree/{item_id}/text")
def set_tree_text(item_id: str, req: TreeTextRequest):
    """A tree written back as it was before a reroot (Ctrl+Z), or after it (Ctrl+Y)."""
    _free(item_id)
    item = _item(item_id)
    if item["kind"] != "tree":
        raise HTTPException(400, f"{item['label']} is not a tree")
    try:
        return {"previous": library.set_tree_text(item, req.text)}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(507, f"Couldn't save tree: {e}")


@router.get("/api/variants/{item_id}")
def get_variants(item_id: str):
    """A VCF item: its text (its reference's lines), the reference's id or why there is none."""
    item = _item(item_id)
    if item["kind"] != "variants":
        raise HTTPException(400, f"{item['label']} is not a VCF")
    return library.variants_data(item)


@router.get("/api/comparison/{item_id}")
def get_comparison(item_id: str):
    item = _item(item_id)
    if item["kind"] != "comparison":
        raise HTTPException(400, f"{item['label']} is not a genome comparison")
    return library.comparison_data(item)


class BlockRequest(BaseModel):
    block: int  # its index in the comparison's blocks


@router.post("/api/comparison/{item_id}/extract")
def extract_block(item_id: str, req: BlockRequest):
    """A block of a comparison as a new alignment."""
    item = _item(item_id)
    if item["kind"] != "comparison" or not 0 <= req.block < len(library.comparison_data(item)["blocks"]):
        raise HTTPException(400, "No such block")
    return {"id": library.extract_block(item, req.block)}


# ── Figures: items that draw a sequence, tree or comparison (library.add_figure) ──
# A figure id is the usual key. A source's id is still accepted (its first figure,
# made on the first save) for the Tree / Figure switch until Figure ▾ replaces it.

def _figure(item_id, create=False):
    """The figure item for an id: the figure itself, or a source's first figure
    (None while it has none, unless create)."""
    item = _item(item_id)
    if item["kind"] == "figure":
        return item
    try:
        library.figure_kind(item)
    except ValueError as e:
        raise HTTPException(400, str(e))
    found = library.figures_of(item)
    if found:
        return found[0]
    return library.get_item(library.add_figure(item)) if create else None


@router.get("/api/figure/{item_id}")
def get_figure(item_id: str):
    """A figure: what it draws (of, source), the part shown and its settings;
    missing when its source is gone."""
    figure = _figure(item_id)
    if figure is None:  # a source without a figure yet
        source = _item(item_id)
        return {"id": None, "of": library.figure_kind(source), "source": source["id"], "part": None,
                "settings": {}, "missing": False}
    data = library.figure_data(figure)
    source = library.figure_source(figure)
    of = data["of"] or (library.figure_kind(source) if source else None)
    return {"id": figure["id"], "of": of, "source": source["id"] if source else None,
            "source_label": source["label"] if source else None, "part": data["part"],
            "settings": data["settings"], "missing": source is None}


class FigureRequest(BaseModel):
    source: str               # the item to draw
    part: dict | None = None  # a clade's tips, a stretch of a genome, some of a comparison's genomes
    settings: dict | None = None  # its first settings (a stretch: a linear map of it; genomes left out: hidden)
    name: str | None = None
    of: str | None = None     # "dotplot": a dot plot of two of its sequences (part {a, b})


@router.post("/api/figures")
def new_figure(req: FigureRequest):
    """A new figure item of a sequence, tree or comparison (in its folder)."""
    try:
        return {"id": library.add_figure(_item(req.source), name=req.name, part=req.part, settings=req.settings, of=req.of)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/figure/{item_id}/history")
def get_figure_history(item_id: str):
    figure = _figure(item_id)
    return library.figure_versions(figure) if figure else {"versions": [], "current": -1}


class FigureHistoryRequest(BaseModel):
    index: int
    expected_hash: str
    target_hash: str
    expected_current: int
    expected_count: int


@router.post("/api/figure/{item_id}/history")
def restore_figure_history(item_id: str, req: FigureHistoryRequest):
    figure = _figure(item_id)
    if figure is None:
        raise HTTPException(409, "This item has no saved figure")
    _free(figure["id"])
    try:
        return library.restore_figure_version(figure, req.index, req.expected_hash,
                                              req.target_hash, req.expected_current, req.expected_count)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@router.put("/api/figure/{item_id}")
def put_figure(item_id: str, settings: dict):
    figure = _figure(item_id, create=True)
    _free(figure["id"])
    try:
        library.save_figure_settings(figure, settings)
    except OSError as e:
        raise HTTPException(507, f"Couldn't save figure: {e}")
    return {"id": figure["id"]}

class FigureExport(BaseModel):
    format: str               # svg, pdf, png, tiff
    svg: str = ""             # the figure (svg, pdf)
    png: str = ""             # the figure drawn at the resolution asked, base64 (png, tiff)
    dpi: int = 300
    path: str | None = None   # app window: where to write; browser: None = the file comes back (base64)


@router.post("/api/figure/export")
def export_figure(req: FigureExport):
    """A figure as a file: SVG as it is, PDF from the SVG (vector), PNG and TIFF
    from the page's drawing at the resolution asked (TIFF LZW-compressed)."""
    try:
        data = figures.export(req.format, req.svg, req.png, req.dpi)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.path:
        Path(req.path).write_bytes(data)
        return {"path": req.path}
    return {"data": base64.b64encode(data).decode("ascii")}


@router.get("/api/ncbi/search")
def ncbi_search(query: str, start: int = 0):
    """GenBank (nucleotide) records matching an NCBI query, a page at a time."""
    if not query.strip():
        raise HTTPException(400, "Type what to search for")
    try:
        return ncbi.search(query.strip(), max(0, start))
    except ValueError as e:
        raise HTTPException(400, str(e))


class ProteinRequest(BaseModel):
    sequence: str          # as edited in the viewer (not necessarily saved)
    window: int = 9        # Kyte-Doolittle window


@router.post("/api/protein/properties")
def protein_properties(req: ProteinRequest):
    """The Properties tab: weight, pI, charge, GRAVY, composition and a hydrophobicity plot."""
    if len(req.sequence) > 200_000:
        raise HTTPException(400, "That protein is too long for the properties tab (over 200,000 letters)")
    try:
        return protein_properties_module.properties(req.sequence, req.window if req.window in protein_properties_module.WINDOWS else 9)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/blast/{item_id}")
def get_blast(item_id: str):
    item = _item(item_id)
    if item["kind"] != "blast":
        raise HTTPException(400, f"{item['label']} is not BLAST results")
    return library.blast_data(item)


PRIMER_MAX = 60  # bp: longer sequences aren't primers


@router.get("/api/primers/sources")
def primer_sources():
    """Primers to fill the Test tab with: designs (each pair), and short DNA
    sequences named ..._F / ..._R anywhere in the library (Save primers' lists,
    split or grouped, or imported back from an export), paired by the rest of the name."""
    designs, found = [], {}
    for item in library.items():
        if item["missing"]:
            continue
        if item["kind"] == "sequence" and not item.get("protein") and 0 < (item.get("length") or 0) <= PRIMER_MAX:
            ends = re.compile(r"(.*?)[ _-]?(F|R|fwd|rev|forward|reverse)$", re.I)  # in the name, or ">cox1 F"
            match = ends.match(item["name"]) or ends.match(f"{item['name']} {item.get('description') or ''}".strip())
            if match:
                side = "forward" if match[2][0].upper() == "F" else "reverse"
                stem = (item["parent"] or "") + "	" + match[1].strip(" _-")
                found.setdefault(stem, {})[side] = item
            continue
        if item.get("parent"):
            continue
        if item["kind"] == "primers" and (item.get("mode") or "design") == "design":
            data = library.primers_data(item)
            pairs = [{"n": i + 1, "forward": pair["left"]["sequence"], "reverse": pair["right"]["sequence"],
                      "product": pair.get("product_size")}
                     for i, pair in enumerate(data.get("pairs") or [])]
            designs.append({"id": item["id"], "label": item["label"], "pairs": pairs})
    lists = []
    for stem, sides in sorted(found.items(), key=lambda kv: kv[0].lower()):
        entry = {"id": "+".join(it["id"] for it in sides.values())}
        for side, it in sides.items():
            entry[side] = str(library.records(it)[0].seq)
        name = stem.split("	")[1]
        parent = next(iter(sides.values()))["label"].rpartition(" › ")[0]
        same = parent.split(" › ")[-1].replace("_", " ") == name.replace("_", " ")  # a pair's own list
        label = parent if same else f"{parent} › {name}" if parent else name
        entry["label"] = label if len(sides) == 2 else f"{label} ({next(iter(sides))} only)"
        lists.append(entry)
    return {"designs": designs, "lists": lists}


@router.get("/api/primers/{item_id}")
def get_primers(item_id: str):
    item = _item(item_id)
    if item["kind"] != "primers":
        raise HTTPException(400, f"{item['label']} is not primer results")
    return library.primers_data(item)


class PrimerRowsRequest(BaseModel):
    rows: list[int]   # 0-based indices into the item's pairs


@router.post("/api/primers/{item_id}/sequences")
def primers_sequences(item_id: str, req: PrimerRowsRequest):
    """The ticked pairs as lists: each with its forward and reverse primer."""
    item = _item(item_id)
    if item["kind"] != "primers":
        raise HTTPException(400, f"{item['label']} is not primer results")
    if (library.primers_data(item).get("mode") or "design") != "design":
        raise HTTPException(400, "Only a primer design's pairs can be made into primers")
    made = library.add_primer_sequences(item, req.rows)
    return {"items": [{"id": it["id"], "label": it["label"]} for it in made]}
