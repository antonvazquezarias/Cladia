"""The library: Cladia's own copies of everything in the list.

Importing a file copies it into <data folder>/library; from then on Cladia only
works on the copy, and the original is never opened again. Files leave Cladia
only through Export. Removing an item deletes Cladia's copy.

Kinds of item, one file each:
  sequence   one record, GenBank
  list       several records, multi-record GenBank (a "sequence list", as in
             Geneious); each record also shows as a child item "<list id>-<n>"
  alignment  aligned FASTA (MAFFT output, or an imported alignment)
  tree       Newick (FastTree/IQ-TREE output, or an imported tree)
  mapping    reads mapped to a reference (a job's result, see mapping.py): JSON,
             plus the BAM (and its index) next to it if it was kept
  blast      BLAST hits, at NCBI or in a local database (tool_plugins.ncbi_blast.run_search): JSON
  comparison progressiveMauve's blocks (tool_plugins.mauve.run_comparison): JSON, plus its XMFA
  primers    primer pairs, or in-silico PCR products (jobs.primer_*_job): JSON

The list itself is stored in <data folder>/.cladia/items.json.
"""

import codecs
import gzip
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from collections import Counter
from contextlib import ExitStack
from io import BytesIO, StringIO
from pathlib import Path

from Bio import AlignIO, Phylo, SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from . import config, formats, gff, graph, history, mapping, metatable, sanger, submitter, tracks
from .genbank import (circularized, cds_protein, decode, end_overlap, editor_to_record, feature_name, file_format, genetic_code, is_protein,
                      looks_protein, own_description,
                      read_records, record_to_editor, records_text, replace_file, sniff, when_free, write_fasta,
                      write_record)

TREE_EXT = {".nwk", ".newick", ".tre", ".tree", ".treefile"}
_scan_cache = {}  # path -> ((mtime, size), (record index, record meta)); avoids re-reading unchanged files


# ── Storage ──────────────────────────────────────────────────────────────
# Jobs run side by side and finish while requests come in: whatever changes the
# items list (items.json) or rewrites a library file goes through one lock, so
# two changes can't overwrite each other (load ... _save).

_writing = threading.RLock()


def writes(func):
    """One change to the library at a time (a decorator)."""
    def locked(*args, **kwargs):
        with _writing:
            return func(*args, **kwargs)
    locked.__name__, locked.__doc__ = func.__name__, func.__doc__
    return locked


def library_dir():
    return config.DATA_DIR / "library"


def _items_file():
    return config.APP_DIR / "items.json"


def ensure_folders():
    library_dir().mkdir(parents=True, exist_ok=True)
    config.APP_DIR.mkdir(exist_ok=True)


def _load_all():
    if not _items_file().exists():
        return {"entries": [], "folders": []}
    return {"folders": [], **json.loads(_items_file().read_text(encoding="utf-8"))}


def load():
    """List of entries: {"id", "file" (name inside the library folder), "kind", "name",
    "folder" (if in one)}."""
    return _load_all()["entries"]


@writes
def set_provenance(item_ids, provenance):
    """Attach a job record to its surviving output items."""
    entries = load()
    for entry in entries:
        if entry["id"] in item_ids:
            entry["provenance"] = provenance
    _save(entries)


def _save(entries, folders=None):
    """folders: the folder names (None: kept as they are)."""
    data = {"entries": entries, "folders": _load_all()["folders"] if folders is None else folders}
    tmp = _items_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(_items_file())


# ── Folders ──────────────────────────────────────────────────────────────
# Folders inside folders (projects): a folder is a path, "Mitocondrias/Gigartinales";
# an item is in one folder or none (the library's top). Tool results go into the
# folder of what they were made from (jobs, duplicate, split...).

def _parent(path):
    return path.rsplit("/", 1)[0] if "/" in path else None


def _join(parent, name):
    return f"{parent}/{name}" if parent else name


def _moved(path, old, new):
    """`path` with folder `old` (and what's in it) now at `new` (None: the top)."""
    if path == old:
        return new
    if path.startswith(old + "/"):
        return _join(new, path[len(old) + 1:])
    return path


def folders():
    """Every folder's path, A to Z (with the folders that hold them, and ones only
    named by an item)."""
    data = _load_all()
    paths = set(data["folders"]) | {e["folder"] for e in data["entries"] if e.get("folder")}
    for path in list(paths):
        while (path := _parent(path)):
            paths.add(path)
    return sorted(paths, key=str.lower)


def _check_name(name, parent, but=None):
    name = name.strip()
    if not name:
        raise ValueError("A folder needs a name")
    if "/" in name:
        raise ValueError("A folder's name can't have a /")
    taken = {f.lower() for f in folders() if _parent(f) == parent and f != but}
    if _join(parent, name).lower() in taken:
        raise ValueError(f"There is a folder called {name} there already")
    return name


@writes
def free_folder_name(name, parent=None):
    """name, or "name 2", "name 3"... if `parent` already has a folder of that name."""
    taken, n, candidate = set(folders()), 2, name
    while _join(parent, candidate) in taken:
        candidate, n = f"{name} {n}", n + 1
    return candidate


@writes
def add_folder(name, parent=None):
    """A new folder in `parent` (None: at the top); returns its path."""
    path = _join(parent, _check_name(name, parent))
    _save(load(), folders() + [path])
    return path


def _notify_folder_change(old, new):
    # Folder edits and job publication share _writing. Import lazily to avoid
    # a module cycle; jobs may already be queued when their destination moves.
    from . import jobs
    jobs.relocate_destinations(old, new)


def _relocate(old, new):
    """Folder `old`, with what's inside it, becomes `new`."""
    entries = load()
    for e in entries:
        if e.get("folder"):
            e["folder"] = _moved(e["folder"], old, new)
    _save(entries, sorted({_moved(f, old, new) for f in folders()}, key=str.lower))
    _notify_folder_change(old, new)


@writes
def rename_folder(path, new_name):
    if path not in folders():
        raise ValueError("The folder no longer exists. Refresh and choose a folder again.")
    parent = _parent(path)
    new = _join(parent, _check_name(new_name, parent, but=path))
    _relocate(path, new)
    return new


@writes
def move_folder(path, parent):
    """A folder (with what's inside) into another folder, or to the top (None)."""
    paths = folders()
    if path not in paths or (parent and parent not in paths):
        raise ValueError("The folder or destination no longer exists. Refresh and choose a folder again.")
    if parent and (parent == path or parent.startswith(path + "/")):
        raise ValueError("A folder can't go inside itself")
    new = _join(parent, _check_name(path.rsplit("/", 1)[-1], parent, but=path))
    _relocate(path, new)
    return new


@writes
def delete_folder(path):
    """The folder goes; what was in it (items and folders) goes up one level."""
    up = _parent(path)
    paths = folders()
    if path not in paths:
        raise ValueError("The folder no longer exists. Refresh and choose a folder again.")
    descendants = [f for f in paths if f.startswith(path + "/")]
    remaining = {f.lower() for f in paths if f != path and not f.startswith(path + "/")}
    for child in descendants:
        destination = _moved(child, path, up)
        if destination.lower() in remaining:
            raise ValueError(f"Cannot remove this folder: {child} would merge with {destination}. "
                             "Move or rename that child folder first.")
    entries = load()
    for e in entries:
        if e.get("folder"):
            e["folder"] = _moved(e["folder"], path, up)
            if not e["folder"]:
                e.pop("folder")
    _save(entries, sorted({m for f in folders() if f != path and (m := _moved(f, path, up))}, key=str.lower))
    _notify_folder_change(path, up)


@writes
def move(item_ids, folder):
    """Items (a list member: its whole list) into a folder, or none (folder None)."""
    ids = set(item_ids) | {i.rsplit("-", 1)[0] for i in item_ids}  # list members: "<list id>-<n>"
    entries = load()
    for e in entries:
        if e["id"] in ids:
            if folder:
                e["folder"] = folder
            else:
                e.pop("folder", None)
    _save(entries, folders() + ([folder] if folder and folder not in folders() else []))


def folder_of(item):
    """The folder an item is in (a list member: its list's), or None."""
    item_id = item["parent"] or item["id"]
    return next((e.get("folder") for e in load() if e["id"] == item_id), None)


class DestinationError(ValueError):
    """A result destination is ambiguous or no longer exists."""


DEFAULT_FOLDER = object()


def result_folder(items, folder=DEFAULT_FOLDER):
    """Combined results need an explicit destination when inputs cross folders."""
    if folder is not DEFAULT_FOLDER:
        folder = folder or None
        if folder is not None and folder not in folders():
            raise DestinationError("The result folder no longer exists. Choose a destination again.")
        return folder
    owners = {item.get("parent") or item["id"] for item in items}
    locations = {entry["id"]: entry.get("folder") for entry in load() if entry["id"] in owners}
    places = {locations.get(owner) for owner in owners}
    if len(places) > 1:
        raise DestinationError("The inputs are in different folders. Choose where to save the result.")
    return next(iter(places), None)


# ── Items ────────────────────────────────────────────────────────────────

def record_index(path):
    """[(start, end, name)]: where each record of a GenBank or FASTA file is (bytes)
    and its name, from one pass over the lines. Cached until the file changes. A list
    can hold thousands of genomes: reading or rewriting one record must not parse the
    whole file. The same pass collects each record's header facts (record_meta)."""
    return _scan(path)[0]


def record_meta(path):
    """Per record: {name, description, organism, accession, length, unit, circular,
    features, cds, genes} from the header lines (no parsing), for labels and cards."""
    return _scan(path)[1]


def _scan(path):
    path = Path(path)
    stat = path.stat()
    key = (stat.st_mtime, stat.st_size)
    cached = _scan_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    genbank = file_format(path) == "genbank"
    starts, metas = [], []
    pos = 0
    section = None  # GenBank: DEFINITION (continues), FEATURES, ORIGIN
    with open(path, "rb") as f:
        for line in f:
            if (genbank and line.startswith(b"LOCUS")) or (not genbank and line.startswith(b">")):
                starts.append(pos)
                metas.append(_header_facts(line, genbank, len(metas)))
                section = None
            elif metas and genbank:
                m = metas[-1]
                if section == "ORIGIN":
                    bases = line.upper()
                    m["_gc"] = m.get("_gc", 0) + bases.count(b"G") + bases.count(b"C")
                    m["_called"] = m.get("_called", 0) + len(bases) - len(bases.translate(None, b"ACGTU"))
                elif line.startswith(b"ORIGIN") or line.startswith(b"//"):
                    section = "ORIGIN"
                elif section == "FEATURES":
                    if line[:5] == b"     " and line[5:6] not in (b" ", b""):
                        kind = line.split()[0]
                        if kind != b"source":
                            m["features"] += 1
                            m["cds"] += kind.lower() == b"cds"  # (any case, as read_records)
                            m["genes"] += kind.lower() == b"gene"
                elif line.startswith(b"FEATURES"):
                    section = "FEATURES"
                elif line.startswith(b"DEFINITION"):
                    m["description"] = _text(line[10:])
                    section = "DEFINITION"
                elif section == "DEFINITION" and line.startswith(b"            "):
                    m["description"] += " " + _text(line)
                elif line.startswith(b"  ORGANISM"):
                    m["organism"] = _text(line[10:])
                elif line.startswith(b"ACCESSION"):
                    words = line.split()
                    m["accession"] = words[1].decode("utf-8", "replace") if len(words) > 1 else ""
                elif line[:1] not in (b" ", b""):
                    section = None
            elif metas:  # FASTA: count the bases (gaps too: an alignment's columns)
                metas[-1]["length"] += len(line.strip())
                bases = line.upper()
                metas[-1]["_gc"] = metas[-1].get("_gc", 0) + bases.count(b"G") + bases.count(b"C")
                metas[-1]["_called"] = metas[-1].get("_called", 0) + len(bases) - len(bases.translate(None, b"ACGTU"))
            pos += len(line)
    if starts:
        starts[0] = 0  # anything before the first record belongs to it
    ends = starts[1:] + [pos]
    for m in metas:
        called = m.pop("_called", 0)
        gc = m.pop("_gc", 0)
        m["gc"] = gc / called if called and m["unit"] == "bp" else None
        m["description"] = own_description(m["description"], m["name"])
        if m["organism"].lower() in (".", "unknown", "unknown."):
            m["organism"] = ""
    result = (list(zip(starts, ends, [m["name"] for m in metas])), metas)
    _scan_cache[path] = (key, result)
    return result


def _text(raw):
    return raw.decode("utf-8", "replace").strip()


def _header_facts(line, genbank, n):
    """The facts on a record's first line (GenBank LOCUS, FASTA >)."""
    words = line[5 if genbank else 1:].split()
    name = words[0].decode("utf-8", "replace") if words else f"record {n + 1}"
    m = {"name": name, "description": "", "organism": "", "accession": "", "length": 0, "unit": "bp",
         "circular": False, "features": 0, "cds": 0, "genes": 0}
    if genbank:
        for i, w in enumerate(words):
            if w in (b"bp", b"aa") and i and words[i - 1].isdigit():
                m["length"], m["unit"] = int(words[i - 1]), w.decode()
        m["circular"] = b"circular" in words
    else:  # FASTA: ">name description [Organism]"
        text = _text(line[1:])
        rest = text[len(name):].strip()
        if rest.endswith("]") and "[" in rest:
            m["organism"] = rest[rest.rindex("[") + 1:-1].strip()
            rest = rest[:rest.rindex("[")].strip()
        m["description"] = rest
    return m


def _record_names(path):
    return [name for _, _, name in record_index(path)]


def read_one(path, i):
    """Record i of a file, parsing only that record."""
    start, end, _ = record_index(path)[i]
    with open(path, "rb") as f:
        f.seek(start)
        text = f.read(end - start).decode("utf-8", "replace")
    return read_records(StringIO(text), file_format(path))[0]


def _splice(path, replacements):
    """Rewrites a file with some records replaced or removed: {index: SeqRecord or
    None}. The other records' bytes are kept as they are; atomic."""
    path = Path(path)
    index = record_index(path)
    data = path.read_bytes()
    pieces, last = [], 0
    for i in sorted(replacements):
        start, end, _ = index[i]
        pieces.append(data[last:start])
        if isinstance(replacements[i], bytes):
            pieces.append(replacements[i])
        elif replacements[i] is not None:
            pieces.append(records_text(replacements[i], file_format(path)).encode("utf-8"))
        last = end
    pieces.append(data[last:])
    staging = path.with_name(path.name + ".saving")
    staging.write_bytes(b"".join(pieces))
    replace_file(staging, path)


def items():
    """Everything in the list. A sequence list also yields one child item per record."""
    result = []
    for entry in load():
        path = library_dir() / entry["file"]
        variants_path = library_dir() / entry["variants"] if entry.get("variants") else None
        changed = max(path.stat().st_mtime if path.exists() else 0,
                      variants_path.stat().st_mtime if variants_path and variants_path.exists() else 0)
        item = {"id": entry["id"], "kind": entry["kind"], "name": entry["name"], "label": entry["name"],
                "path": str(path), "record": 0, "parent": None, "missing": not path.exists(),
                "mtime": changed if path.exists() else None,  # also refresh when an attached VCF changes
                "sources": entry.get("sources"),  # alignments: row name -> where it came from
                "bam": entry.get("bam"),          # mappings: the kept BAM file, if any
                "reads": entry.get("reads"),      # mappings: the reads sidecar (the reads lane)
                "reads_mode": entry.get("reads_mode"),  # mappings: whether the viewer shows reads
                "mode": entry.get("mode"),        # primers: design or test (two sections in the list)
                "xmfa": entry.get("xmfa"),        # comparisons: progressiveMauve's alignment, kept beside it
                "figure": entry.get("figure"),    # (until 5 Nov 2026: a figure's settings beside its source; migrated)
                "source": entry.get("source"),    # figures: the item they draw (a list's, with source_record)
                "source_record": entry.get("source_record"),
                "variants": entry.get("variants"),  # alignments: attached VCF
                "variants_reference": entry.get("variants_reference"),
                "variants_source_hash": entry.get("variants_source_hash"),
                "trace": entry.get("trace"),      # Sanger reads: their trace (sanger.py), kept beside them
                "hits": entry.get("hits"),        # assembly graphs: the last BLAST on their nodes
                "tracks": entry.get("tracks"),    # sequences: bedGraph / WIG data tracks kept beside them
                "chrom": entry.get("chrom"),      # variants (VCF): the name it gives its reference
                "sites": entry.get("sites"),      # variants: how many sites (on that reference)
                "link_note": entry.get("link_note"),  # variants: why it has no reference, or what else the file holds
                "read_files": entry.get("read_files"),  # reads: the FASTQ files beside the report
                "reads_removed": entry.get("reads_removed"),  # reads: files deleted, the report kept
                "folder": entry.get("folder"),    # list members: their list's (copied below)
                "sorted": entry.get("sorted"),    # lists, alignments: the last Sort ({key, descending})
                "original": entry.get("original"),  # imports: the file it was copied from ({path, format})
                "provenance": entry.get("provenance")}
        if entry["kind"] in ("sequence", "list") and not item["missing"]:
            metas = record_meta(path)
            if entry["kind"] == "sequence" and metas:
                item.update(description=metas[0]["description"], organism=metas[0]["organism"],
                            accession=metas[0]["accession"],
                            features=metas[0]["features"],  # how many (the Annotate dialog asks about them)
                            protein=metas[0]["unit"] == "aa",  # (Align hides DNA-only options)
                            length=metas[0]["length"])  # (the viewer shows as many as fit its budget)
        if entry["kind"] == "alignment" and not item["missing"]:  # the list's filter also finds its rows
            item["search"] = "\n".join(f"{m['name']} {m['description']} {m['organism']}" for m in record_meta(path))
        if entry["kind"] == "list" and not item["missing"]:
            item["count"] = len(metas)
            item["protein"] = bool(metas) and metas[0]["unit"] == "aa"
            for i, m in enumerate(metas):
                result.append({**item, "id": f"{entry['id']}-{i}", "kind": "sequence", "name": m["name"],
                               "label": f"{entry['name']} › {m['name']}", "record": i, "parent": entry["id"],
                               "description": m["description"], "organism": m["organism"], "features": m["features"],
                               "accession": m["accession"],
                               "protein": m["unit"] == "aa", "length": m["length"]})
        result.append(item)
    counts = Counter((it["source"], it["source_record"]) for it in result if it["kind"] == "figure")
    labels = {}
    for it in result:  # sources: how many figures draw them (Figure ▾)
        key = (it["parent"] or it["id"], it["name"] if it["parent"] else None)
        labels[key] = it["label"]
        if counts.get(key):
            it["figures"] = counts[key]
    for it in result:  # figures: what they show, under their name in the list
        if it["kind"] == "figure":
            source = labels.get((it["source"], it["source_record"]))
            it["description"] = f"of {source}" if source else "its source is no longer in the library"
    return sorted(result, key=lambda it: it["label"].lower())


def item_map():
    """{id: item} from one pass over the list. Requests that look up many items
    (a viewer's rows, a list's sequences, a whole annotation batch) use this
    instead of rebuilding the list once per lookup."""
    return {item["id"]: item for item in items()}


def version(found=None):
    """A short fingerprint of the list and its folders. The page compares it on
    focus with the one from its last /api/library load, so an unchanged library
    doesn't cost a full fetch and render. found: the already-listed items."""
    found = items() if found is None else found
    digest = hashlib.sha1()
    keys = ("id", "label", "kind", "parent", "record", "path", "mtime", "count", "description",
            "organism", "folder", "missing", "features", "length", "protein", "sorted", "search", "reads_mode", "tracks", "reads_removed")
    for item in found:
        digest.update("\0".join(str(item.get(key, "")) for key in keys).encode("utf-8", "replace"))
        digest.update(b"\n")
    for folder in folders():
        digest.update(("\0" + folder).encode("utf-8", "replace"))
    return digest.hexdigest()


def get_item(item_id):
    for item in items():
        if item["id"] == item_id:
            return item
    raise KeyError(f"Not in the list: {item_id}")


def children(item, all_items=None):
    """The sequence items inside a list; a sequence is its own only child.
    all_items: the listed library, when the caller already has it (avoids listing it again)."""
    if item["kind"] != "list":
        return [item]
    every = items() if all_items is None else all_items
    return sorted((it for it in every if it["parent"] == item["id"]), key=lambda it: it["record"])


def records(item):
    """The SeqRecords an item stands for (one for a sequence, all for a list or alignment)."""
    path = Path(item["path"])
    if item["parent"]:
        return [read_one(path, item["record"])]
    return read_records(path, file_format(path))


# ── Importing ────────────────────────────────────────────────────────────

def _guess_kind(path, recs):
    """FASTA with several equal-length, gapped sequences = an alignment."""
    if file_format(path) == "fasta" and len(recs) > 1:
        if len({len(r) for r in recs}) == 1 and any("-" in str(r.seq) for r in recs):
            return "alignment"
    return "list" if len(recs) > 1 else "sequence"


@writes
def _register(path, kind, extra=None):
    """Adds a file that is already in the library folder to the list; returns its id.
    extra: more fields for the entry, e.g. an alignment's "sources"."""
    entry = {"id": uuid.uuid4().hex[:12], "file": path.name, "kind": kind, "name": path.stem, **(extra or {})}
    _save(load() + [entry])
    return entry["id"]


def _store_text(source, raw, text, target):
    """Copies a file into the library as UTF-8 (byte for byte when it already is;
    source None: it was compressed, the text is written)."""
    if source is None or raw.startswith(codecs.BOM_UTF8) or text.encode("utf-8") != raw:
        target.write_text(text, encoding="utf-8")
    else:
        shutil.copyfile(source, target)


def _import_file(path):
    """One file into the library; returns the new item's id, or a list of ids (a
    NEXUS file with an alignment and a tree). Raises ValueError."""
    raw = path.read_bytes()
    source = path
    if sanger.is_abi(raw):  # a Sanger read (.ab1): a sequence with its trace beside it
        return add_read(path)
    if raw[:1] == b"\t" and raw[5:13] == b"SnapGene":  # a SnapGene file (.dna): stored as GenBank
        return _add_snapgene(raw, path)
    if raw[:2] == b"\x1f\x8b":  # gzip (NCBI and ENA downloads): the file inside
        raw = gzip.decompress(raw)
        path, source = path.with_suffix(""), None  # x.gb.gz is named as x.gb
    text = decode(raw)
    fmt = sniff(text[:65536])
    if fmt == "sbt":  # a submission template belongs to the settings, not the library
        preset = submitter.add_from_text(text, path.stem)
        raise Notice(f"kept as the submitter “{preset['name']}” (Settings › Submitters)")
    if fmt == "nexus":  # an alignment, trees, or both
        ids = []
        if formats.nexus_has_alignment(text):
            ids.append(_add_alignment(formats.read_alignment(text, "nexus"), path.stem))
        if re.search(r"begin\s+trees\s*;", text, re.I):
            ids.append(_add_tree(formats.read_trees(text, "nexus"), path.stem + ("_tree" if ids else "")))
        if not ids:
            raise ValueError("a NEXUS file without an alignment (DATA/CHARACTERS) or TREES block")
        return ids
    if fmt in ("phylip", "stockholm"):
        return _add_alignment(formats.read_alignment(text, fmt), path.stem)
    if fmt == "phyloxml":  # a tree: kept as the Newick it converts to (confidence values as supports)
        try:
            trees = list(Phylo.parse(StringIO(text), "phyloxml"))
        except Exception as e:
            raise ValueError(f"not a readable phyloXML file ({e})")
        return _add_tree([formats.read_trees(formats.newick_text(t), "newick")[0] for t in trees], path.stem)
    if fmt == "blasttab":  # BLAST+ tabular output: one results item per query
        return _add_blast_tabular(text, path.stem)
    if fmt == "vcf":  # variants: an item of its own, linked to the sequence it was called against
        return add_vcf(text, path.stem)
    if fmt in ("bedgraph", "wig"):
        raise ValueError("a data track (bedGraph / WIG) without its sequence: drop the file on a sequence in the list, "
                         "or right-click a sequence > Import annotations or tracks")
    if fmt == "bed":
        raise ValueError("BED annotations without sequences: drop the file on a sequence in the list, "
                         "or right-click a sequence > Import annotations")
    if fmt == "embl":  # stored as GenBank, like everything else
        recs = list(SeqIO.parse(StringIO(text), "embl"))
        if not recs:
            raise ValueError("no sequences found")
        name = recs[0].name if len(recs) == 1 else path.stem
        target = unique_path(library_dir() / f"{safe_name(name)}.gb")
        write_record(recs, target)
        return _register(target, "sequence" if len(recs) == 1 else "list", {"name": name})
    if fmt == "newick" or (fmt is None and path.suffix.lower() in TREE_EXT):
        try:
            trees = formats.read_trees(text, "newick")
        except Exception as e:
            raise ValueError(f"not a readable Newick tree ({e})")
        return _add_tree(trees, path.stem, text if "[" not in text else None)
    if fmt == "genbank":
        # thousands of genomes: stored, then counted (only the first is parsed, as a check)
        target = unique_path(library_dir() / f"{safe_name(path.stem)}.gb")
        _store_text(source, raw, text, target)
        try:
            count = len(record_index(target))
            first = read_one(target, 0) if count else None
        except Exception:
            target.unlink()
            raise
        if not count:
            target.unlink()
            raise ValueError("no sequences found")
        if count > 1:
            return _register(target, "list", {"name": path.stem})
        name = first.name if _good_name(first.name) else path.stem
        named = library_dir() / f"{safe_name(name)}.gb"
        if named != target:  # a single sequence is named after itself
            named = unique_path(named)
            target.replace(named)
        return _register(named, "sequence", {"name": name})
    if fmt == "fastg":  # an assembly graph in FASTG: stored as the GFA it converts to
        text, raw, source = graph.fastg_to_gfa(text), b"", None
        fmt = "gfa"
    if fmt == "gfa":  # an assembly graph: kept as it is (graph.py reads it)
        graph.read_gfa(text)  # (a check)
        target = unique_path(library_dir() / f"{safe_name(path.stem)}.gfa")
        _store_text(source, raw, text, target)
        return _register(target, "graph", {"name": path.stem})
    if fmt == "clustal":  # an alignment (e.g. .aln): kept as aligned FASTA
        recs = [r for r in AlignIO.read(StringIO(text), "clustal")]
        for r in recs:
            r.description = ""
        target = unique_path(library_dir() / f"{safe_name(path.stem)}.fasta")
        write_fasta(recs, target)
        return _register(target, "alignment", {"name": path.stem})
    if fmt == "fasta":
        recs = read_records(StringIO(text), "fasta")
        if not recs:
            raise ValueError("no sequences found")
        kind = _guess_kind(path, recs)
        if kind == "alignment":
            target = unique_path(library_dir() / f"{safe_name(path.stem)}.fasta")
            _store_text(source, raw, text, target)
            return _register(target, "alignment", {"name": path.stem})
        name = recs[0].name if kind == "sequence" and _good_name(recs[0].name) else path.stem
        for r in recs:  # the header's text after the name (not the name again as DEFINITION)
            r.description = own_description(r.description, r.id)
            found = re.match(r"(.*?)\s*\[([^\[\]]+)\]$", r.description)  # "... [Organism]", as NCBI writes it
            if found:
                r.description, r.annotations["organism"] = found.group(1), found.group(2).strip()
        target = unique_path(library_dir() / f"{safe_name(name)}.gb")
        write_record(recs, target)  # FASTA becomes GenBank, so annotations can be saved
        return _register(target, kind, {"name": name})
    if fmt == "gff3":
        features, fasta = gff.read_gff3(text)
        if not fasta.strip():
            raise ValueError("GFF3 annotations without sequences: drop the file on a sequence in the list, "
                             "or right-click a sequence > Import annotations")
        recs = read_records(StringIO(fasta), "fasta")  # annotations and sequences: new sequences
        for r in recs:
            data = record_to_editor(r)
            data["features"] = gff.editor_features(features.get(r.id, []), len(r), False)
            annotated = editor_to_record(data, original=r)
            r.features = annotated.features
        name = recs[0].name if len(recs) == 1 else path.stem
        target = unique_path(library_dir() / f"{safe_name(name)}.gb")
        write_record(recs, target)
        return _register(target, "sequence" if len(recs) == 1 else "list", {"name": name})
    raise ValueError("not a GenBank, GFF3, FASTA, Clustal, Newick or GFA file")


def _add_snapgene(raw, path):
    """A SnapGene .dna file as a GenBank sequence (sequence, features, topology, notes;
    SnapGene's own extras, such as enzyme sites and history, are not kept)."""
    try:
        record = SeqIO.read(BytesIO(raw), "snapgene")
    except Exception as e:
        raise ValueError(f"not a readable SnapGene file ({e})")
    if not _good_name(record.name):
        record.name = record.id = safe_name(path.stem)[:16]
    record.annotations.setdefault("molecule_type", "DNA")
    target = unique_path(library_dir() / f"{safe_name(record.name)}.gb")
    write_record([record], target)
    return _register(target, "sequence", {"name": record.name})


def _add_blast_tabular(text, name):
    """BLAST+ -outfmt 6 as results items (one per query, as a search makes them). The
    file has no query length, subject length, titles or organisms: those columns stay empty;
    a subject's scores are its best hit's and the sum of its hits' bit scores."""
    per_query = {}
    for line in text.splitlines():
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 12:
            continue
        per_query.setdefault(cols[0], {}).setdefault(cols[1], []).append(
            {"identity": float(cols[2]), "align_len": int(cols[3]), "q_from": int(cols[6]), "q_to": int(cols[7]),
             "evalue": float(cols[10]), "bits": float(cols[11])})
    if not per_query:
        raise ValueError("no BLAST hits found")
    ids = []
    for query, subjects in per_query.items():
        hits = []
        for subject, hsps in subjects.items():
            best = max(hsps, key=lambda s: s["bits"])
            hits.append({"accession": subject, "title": "", "organism": "", "taxid": None, "length": None,
                         "max_score": round(best["bits"], 1), "total_score": round(sum(s["bits"] for s in hsps), 1),
                         "cover": None, "evalue": min(s["evalue"] for s in hsps), "identity": round(best["identity"], 2)})
        hits.sort(key=lambda h: -h["max_score"])
        result = {"query_length": None, "hits": hits, "query": {"name": query}, "program": "BLAST (tabular file)",
                  "database": "imported results", "entrez": "", "imported": True, "date": time.strftime("%Y-%m-%d %H:%M")}
        ids.append(add_blast(result, f"{query} BLAST" if len(per_query) > 1 else f"{name} BLAST"))
    return ids if len(ids) > 1 else ids[0]


def _add_alignment(records, name):
    if len(records) < 2:
        raise ValueError("an alignment needs at least two sequences")
    target = unique_path(library_dir() / f"{safe_name(name)}.fasta")
    write_fasta(records, target)
    return _register(target, "alignment", {"name": name})


def _add_tree(trees, name, original=None):
    """One tree as an item: `original` Newick text kept as it is, else written from
    the tree (supports from comments become labels). Files of many trees
    (bootstrap or MCMC samples) are refused: they are for summary tools."""
    if not trees:
        raise ValueError("no tree found")
    if len(trees) > 1:
        raise ValueError(f"{len(trees)} trees in one file (bootstrap or MCMC samples?): Cladia shows single "
                         "trees, e.g. the consensus tree made from them")
    target = unique_path(library_dir() / f"{safe_name(name)}.nwk")
    target.write_text(original if original is not None else formats.newick_text(trees[0]), encoding="utf-8")
    return _register(target, "tree", {"name": name})


def _good_name(name):
    return bool(name) and name != "<unknown name>"


# The format an original file is written back in (Export to original location);
# None: Cladia can't write it (EMBL, Stockholm, Clustal, gzip), so only Export
WRITE_BACK = {"genbank": "genbank", "fasta": "fasta", "gff3": "gff3fasta", "newick": "newick",
              "nexus": "nexus", "phylip": "phylip"}


def _original(path):
    """Where an import came from: its path and the format to write it back in."""
    raw = path.read_bytes()[:65536]
    fmt = None if raw[:2] == b"\x1f\x8b" else WRITE_BACK.get(sniff(decode(raw)))
    return {"path": str(path.resolve()), "format": fmt}


class Notice(Exception):
    """A file that was taken in but made no item (a submission template becomes a
    submitter preset): its message is good news, not an error."""


@writes
def import_paths(paths, folder=None, remember=True, notes=None):
    """Copies files into the library (the format is read from the content, so any
    file ending works), into a folder if given. Each item remembers the file it
    came from (remember=False: an upload's staging copy). Returns (ids of the new items, errors);
    notes (a list), if given, collects what files that made no item became (else they count as errors)."""
    ids, errors = [], []
    for p in paths:
        path = Path(p)
        try:
            new = _import_file(path)
            if remember and not isinstance(new, list):  # (a NEXUS file giving two items can't be written back by one)
                entries = load()
                next(e for e in entries if e["id"] == new)["original"] = _original(path)
                _save(entries)
            ids += new if isinstance(new, list) else [new]
        except Notice as e:
            (notes if notes is not None else errors).append(f"{path.name}: {e}")
        except Exception as e:  # unreadable file: report it, carry on with the rest
            errors.append(f"{path.name}: {e}")
    if folder and ids:
        move(ids, folder)
    return ids, errors


def import_text(filename, text, folder=None, data=None, notes=None):
    """Browser mode (no real file paths): the file's text was uploaded (data: the
    bytes of a binary file, e.g. an .ab1 trace). notes: as import_paths'."""
    staging = config.APP_DIR / "upload" / Path(filename).name
    staging.parent.mkdir(exist_ok=True)
    if data is not None:
        staging.write_bytes(data)
    else:
        staging.write_text(text, encoding="utf-8", newline="")
    try:
        return import_paths([staging], folder, remember=False, notes=notes)
    finally:
        staging.unlink()


# ── Sanger reads ─────────────────────────────────────────────────────────
# A read is a sequence item (its trimmed bases, GenBank) with its trace beside it
# (entry "trace", <name>.trace.json: see sanger.py). Reads stay items of their
# own: a list's records can't carry their traces.

@writes
def add_read(path, name=None):
    """An .ab1 file as a new read, trimmed by quality; returns its id."""
    trace = sanger.read_trace(path)
    name = name or Path(path).stem
    start, end = sanger.trim(trace)
    if end - start < 20:  # nothing good enough: kept whole, the viewer shows its qualities
        start, end = 0, len(trace["bases"])
    target = unique_path(library_dir() / f"{safe_name(name)}.gb")
    write_record(sanger.read_record(trace, name, start, end), target)
    side = unique_path(library_dir() / f"{target.stem}.trace.json")
    side.write_text(json.dumps(sanger.stored(trace, start, end)), encoding="utf-8")
    return _register(target, "sequence", {"name": name, "trace": side.name})


def trace_data(item):
    if not item.get("trace"):
        raise ValueError(f"{item['label']} is not a Sanger read")
    return json.loads((library_dir() / item["trace"]).read_text(encoding="utf-8"))


@writes
def retrim(item, limit):
    """A read trimmed again from its whole trace (limit: the error probability;
    0: not trimmed). Its sequence is rewritten from the base calls: edits and
    annotations made since are lost."""
    trace = trace_data(item)
    start, end = sanger.trim(trace, limit)
    if end - start < 1:
        raise ValueError("nothing of the read is that good: try a higher error limit")
    record = sanger.read_record(trace, item["name"], start, end)
    write_record(record, Path(item["path"]))
    side = library_dir() / item["trace"]
    staging = side.with_name(side.name + ".saving")
    staging.write_text(json.dumps({**trace, "start": start, "end": end}), encoding="utf-8")
    replace_file(staging, side)
    return {"start": start, "end": end, "length": len(trace["bases"])}


def _no_reads(chosen):
    reads = [it["label"] for it in chosen if it.get("trace")]
    if reads:
        raise ValueError(f"Sanger reads stay sequences of their own (a list can't keep their traces): "
                         f"{', '.join(reads[:3])}{'…' if len(reads) > 3 else ''}. Put them in a folder instead.")


def add_output(source, name, kind=None, extra=None):
    """Copies a job's result file into the library; returns the new item's id."""
    source = Path(source)
    target = unique_path(library_dir() / f"{safe_name(name)}{source.suffix.lower()}")
    shutil.copyfile(source, target)
    if kind is None:
        kind = "list" if len(_record_names(target)) > 1 else "sequence"
    return _register(target, kind, {"name": name, **(extra or {})})


def add_sequence(record, name):
    """A record as a new sequence item named `name` (its record named the same)."""
    record.name = re.sub(r"\s+", "_", name.strip()) or "sequence"
    record.id = record.name
    target = unique_path(library_dir() / f"{safe_name(name)}.gb")
    write_record(record, target)
    return _register(target, "sequence", {"name": name.strip() or record.name})


@writes
def add_graph(text, name):
    """GFA text as a new assembly graph item named `name`."""
    graph.read_gfa(text)  # (a check)
    target = unique_path(library_dir() / f"{safe_name(name)}.gfa")
    target.write_text(text, encoding="utf-8")
    return _register(target, "graph", {"name": name.strip()})


@writes
def add_list(records, name):
    """Records as a new sequence list named `name` (each record keeps its own name)."""
    for record in records:
        record.id = record.name
    target = unique_path(library_dir() / f"{safe_name(name)}.gb")
    write_record(records, target)
    return _register(target, "list", {"name": name.strip()})


@writes
def add_comparison(result, name, xmfa):
    """A genome comparison (mauve_job) as a new item, its XMFA kept beside it."""
    target = unique_path(library_dir() / f"{safe_name(name)}.compare.json")
    stem = target.name[: -len(".compare.json")]
    target.write_text(json.dumps(result), encoding="utf-8")
    shutil.copyfile(xmfa, library_dir() / f"{stem}.xmfa")
    return _register(target, "comparison", {"name": name, "xmfa": f"{stem}.xmfa"})


def alignment_variants(item):
    """Read an alignment's attached VCF and detect edits made after detection."""
    if item["kind"] != "alignment" or not item.get("variants"):
        return None
    source = Path(item["path"])
    return {"vcf": (library_dir() / item["variants"]).read_text(encoding="utf-8"),
            "reference": item["variants_reference"],
            "stale": hashlib.sha256(source.read_bytes()).hexdigest() != item.get("variants_source_hash")}


@writes
def save_alignment_variants(item, vcf_text, reference, expected_source_hash=None):
    """Replace the attached VCF without changing alignment bases; keep VCF versions."""
    entries = load()
    entry = next((e for e in entries if e["id"] == item["id"]), None)
    if entry is None or entry["kind"] != "alignment":
        raise ValueError("Choose an alignment for variant detection")
    source = library_dir() / entry["file"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if expected_source_hash is not None and source_hash != expected_source_hash:
        raise ValueError("The alignment changed while variants were being detected; run the tool again")
    rows = read_records(source, "fasta")
    names = [row.id for row in rows]
    if reference not in names or len(set(names)) != len(names):
        raise ValueError("Choose a unique reference row")
    lines = vcf_text.splitlines()
    header = next((line.split("\t") for line in lines if line.startswith("#CHROM\t")), None)
    if not lines or not lines[0].startswith("##fileformat=VCFv4.") or not header or len(header) < 10 or header[:9] != [
            "#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"] or header[9:] != names:
        raise ValueError("VCF header must name every alignment row in order")
    ref_bases = str(rows[names.index(reference)].seq).replace("-", "").replace(".", "").upper()
    for line in lines:
        if line.startswith("#") or not line:
            continue
        fields = line.split("\t")
        if len(fields) != len(header) or fields[0] != reference or not fields[1].isdigit():
            raise ValueError("Invalid VCF variant row")
        pos, allele = int(fields[1]), fields[3].upper()
        if pos < 1 or ref_bases[pos - 1:pos - 1 + len(allele)] != allele:
            raise ValueError("VCF reference allele does not match the alignment reference")
    if not entry.get("variants"):
        entry["variants"] = unique_path(library_dir() / f"{entry['id']}.variants.vcf").name
    path = library_dir() / entry["variants"]
    staging = path.with_name(path.name + ".saving")
    staging.write_text(vcf_text, encoding="utf-8")
    owner = f"{entry['id']}_variants"
    if path.exists():
        history.checkpoint(owner, path)
    else:
        history.checkpoint_bytes(owner, b"")
    with history.protect(path, [owner]):
        replace_file(staging, path)
        history.checkpoint(owner, path)
        entry["variants_reference"] = reference
        entry["variants_source_hash"] = source_hash
        _save(entries)
    return entry["variants"]


# ── Figures (the figure editor, figure.js) ──────────────────────────────
# A figure is an item of its own (kind "figure", <name>.figure.json) that draws
# another item: a nucleotide sequence (a genome map), a tree or a genome
# comparison. It is drawn from that source each time, so edits to it show; only
# the editor's settings and the part shown are kept. A list's sequence is named
# by its list and record name (member ids change when the list does).
# History: each figure is its own owner (its id), like any item. Until 5 Nov 2026
# a figure was a file beside its source (entry "figure"); migrate_sidecar_figures
# turns those into figure items with their saved versions.
# File: {"version": 1, "of": "genome" | "tree" | "comparison", "source": id,
#        "source_record": name (list members), "part": null | {...}, "settings": {...}}

def figure_kind(source):
    """What a figure of this item draws; ValueError when it can't have one."""
    if source["kind"] in ("tree", "comparison"):
        return source["kind"]
    if source["kind"] == "sequence" and not source.get("trace") and not item_is_protein(source):
        return "genome"
    raise ValueError("Figures are of nucleotide sequences, trees and genome comparisons")


def dotplot_source_ok(source):
    """A dot plot figure is kept with the item its sequences come from: a DNA
    sequence (a list's too), a list, an alignment or a genome comparison."""
    if source["kind"] in ("list", "alignment", "comparison"):
        return
    if source["kind"] == "sequence" and not item_is_protein(source):
        return
    raise ValueError("Dot plots are of DNA sequences, lists, alignments and genome comparisons")


def figure_source(figure):
    """The item a figure draws, or None when it is gone (deleted, or its list record renamed)."""
    every = items()
    by_id = {it["id"]: it for it in every}
    source = by_id.get(figure.get("source"))
    if source and figure.get("source_record") is not None:
        source = next((it for it in every if it["parent"] == source["id"] and it["name"] == figure["source_record"]), None)
    return source


def figures_of(source):
    """The figure items drawing this item (oldest first)."""
    key = (source["parent"] or source["id"], source["name"] if source["parent"] else None)
    return [it for it in items() if it["kind"] == "figure" and (it.get("source"), it.get("source_record")) == key]


def figure_data(figure):
    """A figure's file: {of, source, source_record, part, settings}. A saved version
    from before figures were items holds the settings alone; it reads as those."""
    raw = json.loads(Path(figure["path"]).read_text(encoding="utf-8"))
    full = isinstance(raw, dict) and "settings" in raw and "of" in raw
    return {"of": raw["of"] if full else None, "source": figure.get("source"),
            "source_record": figure.get("source_record"),
            "part": raw.get("part") if full else None, "settings": raw["settings"] if full else (raw or {})}


def _write_figure(path, data):
    staging = path.with_name(path.name + ".saving")  # written whole, then swapped in
    staging.write_text(json.dumps({"version": 1, **data}), encoding="utf-8")
    replace_file(staging, path)


def _figure_name(source):
    """"<source> figure", then "<source> figure 2"… among the library's figures."""
    taken = {e["name"] for e in load() if e["kind"] == "figure"}
    base = f"{source['name']} figure"
    name, n = base, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    return name


@writes
def add_figure(source, name=None, part=None, settings=None, history_from=None, of=None):
    """A new figure of `source` in its folder; returns its id. history_from: an
    old sidecar's history owner, moved over to be this figure's history.
    of: "dotplot" for a dot plot (part {a, b}: its two sequences), else the
    source's own kind of figure."""
    if of == "dotplot":
        dotplot_source_ok(source)
        if not isinstance(part, dict) or not part.get("a") or not part.get("b"):
            raise ValueError("A dot plot needs its two sequences")
    elif of is None:
        of = figure_kind(source)
    else:
        raise ValueError(f"Unknown kind of figure: {of}")
    ref = {"source": source["parent"] or source["id"]}
    if source["parent"]:
        ref["source_record"] = source["name"]
    name = name or _figure_name(source)
    target = unique_path(library_dir() / f"{safe_name(name)}.figure.json")
    _write_figure(target, {"of": of, **ref, "part": part, "settings": settings or {}})
    new_id = _register(target, "figure", {"name": name, **ref})
    if history_from and history.directory(history_from).exists():
        shutil.move(str(history.directory(history_from)), str(history.directory(new_id)))
    history.checkpoint(new_id, target)
    return same_folder(new_id, source)


@writes
def save_figure_settings(figure, settings):
    """A figure's settings saved (each save a version of the figure's history)."""
    path = Path(figure["path"])
    data = figure_data(figure)
    if not data["of"]:
        source = figure_source(figure)
        data["of"] = figure_kind(source) if source else None
    data["settings"] = settings
    if data["source_record"] is None:
        del data["source_record"]
    history.checkpoint(figure["id"], path)
    with history.protect(path, [figure["id"]]):
        _write_figure(path, data)
        history.checkpoint(figure["id"], path)


@writes
def figure_versions(figure):
    return history.versions(figure["id"])


@writes
def restore_figure_version(figure, index, expected_hash, target_hash, expected_current, expected_count):
    """One of a figure's saved versions back, if its history is still as the page saw it."""
    timeline = history.versions(figure["id"])
    versions = timeline["versions"]
    if (timeline["current"] != expected_current or len(versions) != expected_count or
        not 0 <= index < len(versions) or timeline["current"] < 0 or
        versions[timeline["current"]]["hash"] != expected_hash or versions[index]["hash"] != target_hash):
        raise ValueError("The figure changed since its history was opened; reopen the list")
    return history.restore(figure["id"], Path(figure["path"]), index)


@writes
def migrate_sidecar_figures():
    """Figures kept beside their source (until 5 Nov 2026) become figure items:
    "<source> figure" in the source's folder, with the same settings and saved
    versions. Each is done whole or not at all: the sidecar goes only after the
    figure item is registered."""
    for entry in [e for e in load() if e.get("figure")]:
        path = library_dir() / entry["figure"]
        try:
            settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            source = get_item(entry["id"])
            figure_kind(source)
        except (OSError, ValueError, KeyError):
            settings = None  # unreadable, or of something that can't have a figure: dropped
        if settings:
            add_figure(source, settings=settings, history_from=f"{entry['id']}_figure")
        entries = load()
        for e in entries:
            if e["id"] == entry["id"]:
                e.pop("figure", None)
        _save(entries)
        when_free(lambda: path.unlink(missing_ok=True))


def comparison_data(item):
    return json.loads(Path(item["path"]).read_text(encoding="utf-8"))


@writes
def extract_block(item, index):
    """One block of a comparison as a new alignment: a row per genome in it, named
    after the genome (_R_ first when its bases are reverse complemented, as MAFFT
    names them), with sources, so the rows get their genomes' features."""
    data = comparison_data(item)
    block = data["blocks"][index]
    xmfa = formats.read_xmfa((library_dir() / item["xmfa"]).read_text(encoding="utf-8"))[block["xmfa"]]
    rows, sources = [], {}
    for p in xmfa["parts"]:
        genome = data["genomes"][p["genome"]]
        n = len(genome["data"]["sequence"])
        name = genome["name"].replace(" ", "_")
        # reversed against the library sequence: on the - strand of a genome compared as
        # it is, or on the + strand of one reverse complemented to line up (mauve_job)
        reverse = (p["strand"] < 0) != bool(genome.get("flipped"))
        rows.append(SeqRecord(Seq(p["seq"].ljust(xmfa["columns"], "-")), id=("_R_" if reverse else "") + name,
                              description=""))
        # the row's place in its genome (reverse complemented: in that strand); "turn":
        # the library sequence turned first to start there, as it was compared
        sources[name] = {"item": genome["item"], "offset": p["start"] - 1 if p["strand"] > 0 else n - p["end"],
                         **({"turn": genome["start"]} if genome.get("start") else {})}
    target = unique_path(library_dir() / f"{safe_name(item['name'])}_block{index + 1}.fasta")
    write_fasta(rows, target)
    return same_folder(_register(target, "alignment", {"name": f"{item['name']} block {index + 1}", "sources": sources}), item)


def add_mapping(result, name, bam=None, reads=None):
    """A mapping job's result as a new item; bam: the BAM to keep with it (and its
    index); reads: the reads sidecar (one line a read) the view reads windows from."""
    target = unique_path(library_dir() / f"{safe_name(name)}.mapping.json")
    stem = target.name[: -len(".mapping.json")]
    target.write_text(json.dumps(result), encoding="utf-8")
    extra = {"name": name}
    if bam:
        shutil.copyfile(bam, library_dir() / f"{stem}.bam")
        shutil.copyfile(f"{bam}.bai", library_dir() / f"{stem}.bam.bai")
        extra["bam"] = f"{stem}.bam"
    if reads:
        shutil.copyfile(reads, library_dir() / f"{stem}.reads.tsv")
        extra["reads"] = f"{stem}.reads.tsv"
    return _register(target, "mapping", extra)


@writes
def set_mapping_reads_mode(item_id, mode):
    """Keep a mapping viewer's reads choice with that mapping across app restarts."""
    if mode not in ("none", "all"):
        raise ValueError("Reads mode must be 'none' or 'all'")
    entries = load()
    entry = next((e for e in entries if e["id"] == item_id), None)
    if not entry or entry.get("kind") != "mapping":
        raise ValueError("No such mapping")
    if mode == "all" and not entry.get("reads"):
        raise ValueError("This mapping has no saved reads")
    entry["reads_mode"] = mode
    _save(entries)
    return mode


@writes
def add_blast(results, name):
    """BLAST results (tool_plugins.ncbi_blast.run_search) kept as an item."""
    target = unique_path(library_dir() / f"{safe_name(name)}.blast.json")
    target.write_text(json.dumps(results), encoding="utf-8")
    return _register(target, "blast", {"name": name})


def graph_data(item):
    """An assembly graph item read (graph.read_gfa), cached by file time."""
    path = Path(item["path"])
    key = (str(path), path.stat().st_mtime_ns)
    if _graphs.get("key") != key:
        _graphs.update(key=key, graph=graph.read_gfa(decode(path.read_bytes())))
    return _graphs["graph"]


_graphs = {}  # the last graph read


@writes
def set_graph_hits(item, result):
    """A graph's BLAST hits (internal_jobs.graph_blast_job), in <name>.hits.json beside it."""
    entries = load()
    entry = next(e for e in entries if e["id"] == item["id"])
    name = entry.get("hits") or unique_path(library_dir() / f"{Path(entry['file']).stem}.hits.json").name
    (library_dir() / name).write_text(json.dumps(result), encoding="utf-8")
    entry["hits"] = name
    _save(entries)


@writes
def clear_graph_hits(item):
    """Takes a graph's comparison (its hits file) away; the reference sequence is untouched."""
    entries = load()
    entry = next(e for e in entries if e["id"] == item["id"])
    name = entry.pop("hits", None)
    if name:
        when_free(lambda: (library_dir() / name).unlink(missing_ok=True))
    _save(entries)


# ── Variants (VCF files as items) ─────────────────────────────────────────
# A VCF is an item of its own (kind "variants", <name>.vcf, kept as imported)
# linked to the library sequence it was called against (entry "source", and
# "source_record" for a list's sequence, as figures are), so a sequence can
# have several (callers, sample sets, filtered and not). A VCF names its
# reference by CHROM only: the link is made on import to the one sequence of
# that name whose bases agree with the REF alleles; the entry keeps "chrom"
# (the VCF's name for it) and "sites". Without a match it is kept unlinked.

VCF_CHECK = 500         # REF alleles compared with the sequence on import
VCF_MISMATCH = 0.05     # more than this share disagreeing: not that sequence


def vcf_sites(text):
    """{chrom: site count} and the ##contig lengths of a VCF."""
    counts, lengths = {}, {}
    for line in text.splitlines():
        if line.startswith("##contig=<"):
            m = re.search(r"ID=([^,>]+)", line)
            n = re.search(r"length=(\d+)", line)
            if m and n:
                lengths[m.group(1)] = int(n.group(1))
        elif line and not line.startswith("#"):
            chrom = line.split("\t", 1)[0]
            counts[chrom] = counts.get(chrom, 0) + 1
    return counts, lengths


def vcf_mismatches(text, chrom, sequence):
    """How many of the first VCF_CHECK sites' REF alleles disagree with the sequence, and of how many."""
    checked = wrong = 0
    sequence = sequence.upper()
    for line in text.splitlines():
        if not line or line.startswith("#") or not line.startswith(chrom + "\t"):
            continue
        fields = line.split("\t")
        if len(fields) < 5 or not fields[1].isdigit():
            continue
        pos, ref = int(fields[1]), fields[3].upper()
        if set(ref) - set("ACGT"):
            continue
        checked += 1
        wrong += sequence[pos - 1:pos - 1 + len(ref)] != ref
        if checked >= VCF_CHECK:
            break
    return wrong, checked


def _vcf_reference(text):
    """(sequence item, chrom, note) the VCF was called against, or (None, None, why not)."""
    counts, lengths = vcf_sites(text)
    if not counts:
        raise ValueError("no variant sites in this VCF")
    candidates = [it for it in items() if it["kind"] == "sequence" and not it["missing"] and not item_is_protein(it)]
    named = [(it, _names(it)) for it in candidates]
    reasons = []
    for chrom in sorted(counts, key=counts.get, reverse=True):  # the reference with most sites first
        key = chrom.lower()
        hits = [it for it, names in named if key in names or key.split(".")[0] in names]
        for it in hits:
            seq = str(records(it)[0].seq)
            if chrom in lengths and lengths[chrom] != len(seq):
                reasons.append(f"{it['label']} is {len(seq):,} bp, the VCF's {chrom} {lengths[chrom]:,}")
                continue
            wrong, checked = vcf_mismatches(text, chrom, seq)
            if checked and wrong / checked > VCF_MISMATCH:
                reasons.append(f"{wrong} of {checked} REF alleles differ from {it['label']}")
                continue
            note = f"{len(counts) - 1} other sequence{'s' if len(counts) > 2 else ''} in the file not shown" if len(counts) > 1 else ""
            return it, chrom, note
    if not reasons:
        reasons.append(f"no sequence in the library is called {', '.join(list(counts)[:3])}")
    return None, None, "; ".join(reasons)


@writes
def add_vcf(text, name):
    """A VCF as a variants item, linked to its reference when it is found. Returns the id."""
    reference, chrom, note = _vcf_reference(text)
    counts, _ = vcf_sites(text)
    target = unique_path(library_dir() / f"{safe_name(name)}.vcf")
    target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    extra = {"name": name, "chrom": chrom, "sites": counts.get(chrom) if chrom else sum(counts.values()),
             "link_note": note or None}
    if reference:
        extra["source"] = reference["parent"] or reference["id"]
        if reference["parent"]:
            extra["source_record"] = reference["name"]
    new_id = _register(target, "variants", extra)
    history.checkpoint(new_id, target)
    return same_folder(new_id, reference) if reference else new_id


def variants_data(item):
    """A variants item: its VCF (only its reference's lines when linked), the
    reference (an id, or None when gone or never found) and why not."""
    text = Path(item["path"]).read_text(encoding="utf-8")
    reference = figure_source(item) if item.get("source") else None
    chrom = item.get("chrom")
    if chrom:
        text = "\n".join(l for l in text.splitlines() if l.startswith("#") or l.startswith(chrom + "\t")) + "\n"
    return {"vcf": text, "chrom": chrom, "reference": reference["id"] if reference else None,
            "reference_label": reference["label"] if reference else None,
            "note": item.get("link_note") or ("" if reference or not item.get("source") else "its sequence is no longer in the library")}


# ── Reads (FASTQ samples, reads.py) ───────────────────────────────────────
# Entry "file": the manifest (<sample>.reads.json, the report); "read_files":
# the FASTQ files beside it, as they came. No saved versions of those.

@writes
def add_reads(manifest, read_files, name):
    target = unique_path(library_dir() / f"{safe_name(name)}.reads.json")
    target.write_text(json.dumps(manifest), encoding="utf-8")
    return _register(target, "reads", {"name": name, "read_files": list(read_files)})


def reads_data(item):
    data = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
    present = [n for n in item.get("read_files") or [] if (library_dir() / n).exists()]
    return {**data, "present": present, "paths": [str(library_dir() / n) for n in present],
            "removed": bool(item.get("reads_removed"))}


@writes
def drop_read_files(item):
    """The read files deleted (the space they take); the report stays."""
    entries = load()
    entry = next(e for e in entries if e["id"] == item["id"])
    gone = entry.get("read_files") or []
    for name in gone:
        when_free(lambda n=name: (library_dir() / n).unlink(missing_ok=True))
    entry["read_files"], entry["reads_removed"] = [], True
    _save(entries)
    return len(gone)


# ── Data tracks (bedGraph / WIG, tracks.py) ──────────────────────────────
# Kept beside a sequence (entry "tracks", <name>.tracks.json), several per
# sequence; the viewer draws them under the ruler. Standalone sequences only:
# a list's records can't keep files beside them.

def data_tracks(item):
    path = library_dir() / item["tracks"] if item.get("tracks") else None
    return json.loads(path.read_text(encoding="utf-8"))["tracks"] if path and path.exists() else []


@writes
def _save_tracks(item, every):
    entries = load()
    entry = next(e for e in entries if e["id"] == item["id"])
    if not every:
        name = entry.pop("tracks", None)
        if name:
            when_free(lambda: (library_dir() / name).unlink(missing_ok=True))
    else:
        name = entry.get("tracks") or unique_path(library_dir() / f"{Path(entry['file']).stem}.tracks.json").name
        path = library_dir() / name
        staging = path.with_name(path.name + ".saving")
        staging.write_text(json.dumps({"tracks": every}), encoding="utf-8")
        replace_file(staging, path)
        entry["tracks"] = name
    _save(entries)


def add_tracks(text, target, filename="track"):
    """A bedGraph or WIG file's values onto `target` (its only chromosome, or the
    one naming the sequence). Returns the names of the tracks added."""
    if target["kind"] != "sequence" or target["parent"]:
        raise ValueError("data tracks go on sequences of their own; take this one out of its list first")
    if item_is_protein(target):
        raise ValueError("data tracks are for nucleotide sequences")
    by_chrom, name = tracks.read_tracks(text, Path(filename).stem)
    if len(by_chrom) == 1:
        intervals = next(iter(by_chrom.values()))
    else:
        names = _names(target)
        hit = [c for c in by_chrom if c.lower() in names or c.lower().split(".")[0] in names]
        if len(hit) != 1:
            raise ValueError(f"the file has values for {len(by_chrom)} sequences ({', '.join(list(by_chrom)[:5])}…) "
                             f"and none is called {target['name']}")
        intervals = by_chrom[hit[0]]
    track = {"name": name, "file": Path(filename).name, **tracks.fit(intervals, item_meta(target)["length"])}
    every = data_tracks(target)
    taken = {t["name"] for t in every}
    base, n = track["name"], 2
    while track["name"] in taken:
        track["name"], n = f"{base} {n}", n + 1
    _save_tracks(target, every + [track])
    return [track["name"]]


def remove_track(item, name):
    every = data_tracks(item)
    if not any(t["name"] == name for t in every):
        raise ValueError(f"no track called {name}")
    _save_tracks(item, [t for t in every if t["name"] != name])


def graph_hits(item):
    path = library_dir() / item["hits"] if item.get("hits") else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None


def blast_data(item):
    return json.loads(Path(item["path"]).read_text(encoding="utf-8"))


@writes
def add_primers(result, name):
    """Primer results (tool_plugins.primer3.run_design, primer_test_job) kept as an item;
    "mode" (design/test) goes in the list too: the two are separate sections."""
    target = unique_path(library_dir() / f"{safe_name(name)}.primers.json")
    target.write_text(json.dumps(result), encoding="utf-8")
    return _register(target, "primers", {"name": name, "mode": result.get("mode", "design")})


def fix_primer_modes():
    """Entries made before the mode was kept: read it from each item's file once."""
    entries = load()
    changed = False
    for entry in entries:
        if entry.get("kind") == "primers" and "mode" not in entry:
            try:
                entry["mode"] = json.loads((library_dir() / entry["file"]).read_text(encoding="utf-8")).get("mode", "design")
            except (OSError, ValueError):
                continue
            changed = True
    if changed:
        _save(entries)


def primers_data(item):
    return json.loads(Path(item["path"]).read_text(encoding="utf-8"))


@writes
def add_primer_sequences(item, rows):
    """Each ticked pair as a list of its two primers (forward, reverse), beside it.

    rows: 0-based indices into the item's pairs. Returns the new items."""
    data = primers_data(item)
    pairs = data.get("pairs") or []
    label = (data.get("template") or {}).get("label") or item["name"]
    made = []
    for i in rows:
        if not 0 <= i < len(pairs):
            continue
        pair = pairs[i]
        records = []
        for side, end in (("left", "F"), ("right", "R")):
            primer = pair[side]
            record = SeqRecord(Seq(primer["sequence"]), id="", name="",
                               description=f"Primer pair {i + 1} {end}, Tm {primer['tm']:.1f} C, "
                                           f"GC {primer['gc']:.0f} %, product {pair['product_size']} bp "
                                           f"({pair['product_start']}-{pair['product_end']})")
            record.annotations.update(molecule_type="DNA", topology="linear")
            record.name = record.id = safe_name(f"{label} pair {i + 1} {end}")
            records.append(record)
        name = f"{label} pair {i + 1}"
        target = unique_path(library_dir() / f"{safe_name(name)}.gb")
        write_record(records, target)
        new_id = _register(target, "list", {"name": name})
        move([new_id], folder_of(item))
        made.append(get_item(new_id))
    return made


def item_is_protein(item):
    """A sequence (or list) of amino acids."""
    return is_protein(records(children(item)[0])[0])


def item_meta(item):
    """A sequence item's header facts (see record_meta)."""
    return record_meta(Path(item["path"]))[item["record"]]


def alignment_rows(item):
    """An alignment's rows with what is known about each: its header's description
    and [organism], or better, those of the library sequence it came from."""
    path = Path(item["path"])
    by_id = {it["id"]: it for it in items()}
    rows = []
    for record, meta in zip(read_records(path, "fasta"), record_meta(path)):
        row = {"name": record.id, "sequence": str(record.seq),
               "description": meta["description"], "organism": meta["organism"]}
        source_id = row_source(item, record.id)[0]
        source = by_id.get(source_id)
        if source and source["kind"] == "sequence" and not source["missing"]:
            row["description"] = source.get("description") or row["description"]
            row["organism"] = source.get("organism") or row["organism"]
        rows.append(row)
    return rows


def info(item):
    """What the list's hover card shows about an item."""
    kind = item["kind"]
    if item["missing"]:
        return {"kind": kind, "missing": True}
    if kind == "sequence":
        return {"kind": kind, **item_meta(item)}
    if kind == "list":
        metas = record_meta(Path(item["path"]))
        lengths = [m["length"] for m in metas]
        organisms = Counter(m["organism"] for m in metas if m["organism"])
        return {"kind": kind, "count": len(metas), "total": sum(lengths), "min": min(lengths, default=0),
                "max": max(lengths, default=0), "unit": metas[0]["unit"] if metas else "bp",
                "organisms": organisms.most_common(4), "organism_count": len(organisms)}
    if kind == "alignment":
        rows = alignment_rows(item)
        organisms = Counter(r["organism"] for r in rows if r["organism"])
        return {"kind": kind, "count": len(rows), "columns": max((len(r["sequence"]) for r in rows), default=0),
                "organisms": organisms.most_common(4), "organism_count": len(organisms)}
    if kind == "tree":
        tree = Phylo.read(StringIO(Path(item["path"]).read_text(encoding="utf-8")), "newick")
        return {"kind": kind, "tips": tree.count_terminals()}
    if kind == "mapping":
        data = mapping_data(item)
        return {"kind": kind, "reference": data["reference"]["name"], **{k: data["summary"][k] for k in
                ("reads", "mapped_percent", "mean_depth", "covered_percent", "difference_count")}}
    if kind == "comparison":
        data = comparison_data(item)
        return {"kind": kind, "genomes": [g["name"] for g in data["genomes"]], "blocks": len(data["blocks"])}
    if kind == "graph":
        return {"kind": kind, **graph.summary(graph_data(item))}
    if kind == "blast":
        data = blast_data(item)
        top = data["hits"][0] if data["hits"] else {}
        return {"kind": kind, "query": data["query"]["name"], "program": data["program"], "database": data["database"],
                "date": data["date"], "hits": len(data["hits"]), "top": top.get("title", ""),
                "top_identity": top.get("identity")}
    if kind == "primers":
        data = primers_data(item)
        params = data.get("parameters") or {}
        if data.get("mode") == "design":
            return {"kind": kind, "mode": "design", "template": (data.get("template") or {}).get("label", ""),
                    "pairs": len(data.get("pairs") or []), "product_min": params.get("product_min"),
                    "product_max": params.get("product_max"), "tm_min": params.get("tm_min"),
                    "tm_max": params.get("tm_max"), "date": data.get("date", "")}
        return {"kind": kind, "mode": "test", "forward": data.get("forward", ""),
                "reverse": data.get("reverse", ""), "products": len(data.get("hits") or []),
                "mismatches": params.get("mismatches", 0), "date": data.get("date", "")}
    return {"kind": kind}


def mapping_data(item):
    return json.loads(Path(item["path"]).read_text(encoding="utf-8"))


def consensus_record(item):
    """A mapping's consensus as a record, with the reference's features moved onto
    it (and the reference's metadata, if it is still in the list)."""
    data = mapping_data(item)
    per_position = data["consensus_ref"]
    insertions = {int(k): v for k, v in data["insertions"].items()}
    seq, where = mapping.consensus_sequence(per_position, insertions)
    ref = data["reference"]
    features = mapping.lift_features(ref["features"], per_position, where, len(per_position))
    try:
        original = records(get_item(data["reference_item"]))[0]
    except KeyError:
        original = None
    name = f"{item['name']} consensus"
    record = editor_to_record({"name": name, "sequence": seq, "features": features,
                               "circular": ref.get("circular"), "geneticCode": ref.get("geneticCode")},
                              original=original)
    record.id = record.name
    record.description = f"Consensus of reads mapped to {ref['name']}"
    return record, name


def same_folder(new_id, source):
    """A new item made from `source` goes into its folder; returns the new id."""
    folder = folder_of(source)
    if folder:
        move([new_id], folder)
    return new_id


@writes
def duplicate(item):
    """A copy of an item, named "<name> copy". A sequence of a list becomes a
    sequence of its own."""
    name = f"{item['name']} copy"
    if item["kind"] == "reads":
        raise ValueError("Reads aren't duplicated (their files are large): import them again if you need a second copy")
    if item["parent"]:
        return same_folder(add_sequence(records(item)[0], name), item)
    if item["kind"] == "mapping":
        bam = library_dir() / item["bam"] if item.get("bam") else None
        reads = library_dir() / item["reads"] if item.get("reads") else None
        return same_folder(add_mapping(mapping_data(item), name, bam, reads), item)
    source = Path(item["path"])
    target = unique_path(library_dir() / f"{safe_name(name)}{''.join(source.suffixes[-2:]) if item['kind'] == 'comparison' else source.suffix.lower()}")
    shutil.copyfile(source, target)
    extra = {"name": name, **({"sources": item["sources"]} if item.get("sources") else {}),
             **({"mode": item["mode"]} if item.get("mode") else {}),
             **{k: item[k] for k in ("source", "source_record") if item.get(k) is not None}}  # a figure: the same source
    for key in ("xmfa", "figure", "variants", "trace", "hits", "tracks"):  # files kept beside their owner
        if item.get(key):
            copy = unique_path(library_dir() / f"{target.name.split('.')[0]}{''.join(Path(item[key]).suffixes)}")
            shutil.copyfile(library_dir() / item[key], copy)
            extra[key] = copy.name
    if item.get("variants"):
        extra["variants_reference"] = item["variants_reference"]
        extra["variants_source_hash"] = item["variants_source_hash"]
    new_id = _register(target, item["kind"], extra)
    if extra.get("variants"):
        history.checkpoint(f"{new_id}_variants", library_dir() / extra["variants"])
    same_folder(new_id, item)
    if item["kind"] == "sequence":
        rename(get_item(new_id), name)  # its record too
    return new_id


# ── Annotations from GFF3 ────────────────────────────────────────────────

@writes
def add_features(item, features):
    """Features (from gff.read_gff3) added to a sequence item; ones it already has
    (same type and place) are skipped. Returns (added, skipped)."""
    record = records(item)[0]
    data = record_to_editor(record)
    n = len(record)

    def place(f):  # type and bases covered, parts across the origin split (as they are stored)
        spans = []
        for l in f.get("locations") or [f]:
            spans += [(l["start"], l["end"])] if l["start"] <= l["end"] else [(l["start"], n - 1), (0, l["end"])]
        return f["type"], tuple(sorted(spans))

    have = {place(f) for f in data["features"]}
    new = [f for f in gff.editor_features(features, n, data["circular"]) if place(f) not in have]
    if new:
        data["features"] += new
        save_record(item, editor_to_record(data, original=record))
    return len(new), len(features) - len(new)


def _names(item):
    """What a GFF3 seqid may call a sequence: its name in the list, LOCUS name, accession."""
    meta = item_meta(item)
    names = {item["name"], meta.get("name"), meta.get("accession")}
    names = {n.lower() for n in names if n}
    return names | {n.split(".")[0] for n in names}  # accessions with or without the version


def import_annotations(text, target=None):
    """Annotations from GFF3 text: onto `target` (a sequence item: the file's only
    seqid, or the one naming it), or else each seqid onto the sequence in the list
    it names. Returns (results [{id, label, added, skipped}], errors)."""
    features, _ = gff.read_gff3(text)
    if not features:
        raise ValueError("no features in this GFF3 file")
    if target:
        if len(features) == 1:
            pairs = [(target, next(iter(features.values())))]
        else:
            names = _names(target)
            hit = [s for s in features if s.lower() in names or s.lower().split(".")[0] in names]
            if len(hit) != 1:
                raise ValueError(f"the file has {len(features)} sequences ({', '.join(list(features)[:5])}…) "
                                 f"and none is called {target['name']}")
            pairs = [(target, features[hit[0]])]
        errors = []
    else:
        sequences = [it for it in items() if it["kind"] == "sequence" and not it["missing"]]
        named = [(it, _names(it)) for it in sequences]
        pairs, errors = [], []
        for seqid, feats in features.items():
            key = seqid.lower()
            hit = [it for it, names in named if key in names or key.split(".")[0] in names]
            if len(hit) == 1:
                pairs.append((hit[0], feats))
            else:
                errors.append(f"{seqid}: {'no sequence in Cladia has this name' if not hit else f'{len(hit)} sequences have this name'}")
    results = []
    for item, feats in pairs:
        try:
            added, skipped = add_features(item, feats)
        except ValueError as e:
            errors.append(f"{item['label']}: {e}")
            continue
        results.append({"id": item["id"], "label": item["label"], "added": added, "skipped": skipped})
    return results, errors


# ── Changing and removing ────────────────────────────────────────────────

@writes
def remove_annotations(item_ids):
    """Every feature but the source taken off these sequences (a list: all of its
    sequences, in one rewrite of its file). Returns how many features went."""
    gone = 0
    for item in [get_item(i) for i in item_ids]:
        members = children(item)
        new = {}
        for m in members:
            record = records(m)[0]
            kept = [f for f in record.features if f.type == "source"]
            gone += len(record.features) - len(kept)
            record.features = kept
            new[m["record"]] = record
        if item["kind"] == "list":
            _splice(Path(item["path"]), new)
        else:
            for m in members:
                save_record(m, new[m["record"]])
    return gone


@writes
def circularize(item_ids, min_overlap=30):
    """Linear sequences whose end repeats their start: the repeat trimmed, marked circular
    (saved in place, versions kept). Returns {done: [{label, trimmed, dropped}], circular,
    none}: sequences changed, already circular, and without such an overlap."""
    done, circular, none = [], 0, 0
    for item in [get_item(i) for i in item_ids]:
        for m in children(item):
            record = records(m)[0]
            if record.annotations.get("topology") == "circular":
                circular += 1
                continue
            size = end_overlap(record.seq, min_overlap)
            if not size:
                none += 1
                continue
            new, dropped = circularized(record, size)
            save_record(m, new)
            done.append({"label": m["label"], "trimmed": size, "dropped": dropped})
    return {"done": done, "circular": circular, "none": none}


# A sequence of a big list keeps versions of its own record, not of the whole file:
# a version of a 191 MB list took ~13 s and ~50 MB for every save of one sequence.
BIG_LIST = 20 * 2 ** 20  # bytes


def _record_history(item):
    """The record timeline of a list's sequence, when it has (or should have) one."""
    if not item.get("parent"):
        return None
    owner = history.record_owner(item["parent"], item["name"])
    big = Path(item["path"]).stat().st_size > BIG_LIST
    return owner if big or history.directory(owner).joinpath("index.json").exists() else None


@writes
def save_record(item, record):
    """Writes an edited sequence back into its item (a list keeps its other records)."""
    path = Path(item["path"])
    record_owner = _record_history(item)
    if record_owner:  # a big list: this record's versions only
        history.checkpoint_bytes(record_owner, _record_bytes(item))
        with history.protect(path, [record_owner]):
            _splice(path, {item["record"]: record})
            history.checkpoint_bytes(record_owner, _record_bytes(get_item(item["id"])))
        return
    owner = item.get("parent") or item["id"]
    history.checkpoint(owner, path)
    with history.protect(path, [owner]):
        if item["parent"]:
            _splice(path, {item["record"]: record})
        else:
            write_record(record, path)
        history.checkpoint(owner, path)


@writes
def saved_versions(item):
    record_owner = _record_history(item)
    if record_owner:
        return {**history.versions(record_owner), "owner": record_owner, "whole_list": False}
    owner = item.get("parent") or item["id"]
    return {**history.versions(owner), "owner": owner, "whole_list": bool(item.get("parent"))}


@writes
def restore_version(item, index, whole=False):
    """A saved version back: of the item's file (whole: the list's, for a list's
    sequence), or of its own record when it keeps those (big lists)."""
    record_owner = None if whole else _record_history(item)
    if record_owner:
        timeline = history.versions(record_owner)
        if not 0 <= index < len(timeline["versions"]):
            raise ValueError("No such saved version")
        if hashlib.sha256(_record_bytes(item)).hexdigest() != history.current_hash(record_owner):
            raise ValueError("The sequence changed outside its history; reload before restoring")
        _splice(Path(item["path"]), {item["record"]: history.data(record_owner, index)})
        history.move_to(record_owner, index)
        return history.versions(record_owner)
    owner = item.get("parent") or item["id"]
    return history.restore(owner, item["path"], index)


@writes
def annotation_baselines(items):
    """Capture each source file at submission, including the order of list records."""
    hashes = {}
    for item in items:
        path = item["path"]
        if path not in hashes:
            hashes[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        item["annotation_hash"] = hashes[path]


@writes
def apply_annotation_group(results, busy):
    """Apply results for one source file only if it is unchanged and not being edited."""
    first = results[0][0]
    owner = first.get("parent") or first["id"]
    try:
        current = [get_item(item["id"]) for item, _ in results]
        path = Path(current[0]["path"])
        if (busy([owner] + [it["id"] for it in current]) or
            any(it["path"] != first["path"] or it["record"] != original["record"]
                for it, (original, _) in zip(current, results)) or
            hashlib.sha256(path.read_bytes()).hexdigest() != first["annotation_hash"]):
            return False
        replacements = {it["record"]: read_records(Path(result), "genbank")[0]
                        for it, (_, result) in zip(current, results)}
        for it in current:
            original = records(it)[0]
            result = replacements[it["record"]]
            old_seq, new_seq = str(original.seq).upper(), str(result.seq).upper()
            if new_seq != old_seq:
                # Never applied in place: changed bases, nor a circular genome the annotation
                # program rotated (its origin would move; the user wants those as copies).
                return False
            result.id, result.name, result.description = original.id, original.name, original.description
            topology = result.annotations.get("topology")
            result.annotations = {**result.annotations, **original.annotations}
            if topology:
                result.annotations["topology"] = topology
            result.dbxrefs = list(original.dbxrefs)
        history.checkpoint(owner, path)
        with history.protect(path, [owner]):
            if first.get("parent"):
                _splice(path, replacements)
            else:
                write_record(replacements[0], path)
            history.checkpoint(owner, path)
        return True
    except (KeyError, FileNotFoundError):
        return False


@writes
def _source_feature(record):
    """The record's source feature (the first; GenBank has one), or None."""
    for feature in record.features:
        if feature.type == "source":
            return feature
    return None


def record_metadata(item):
    """The editable header fields of a sequence record (what the dialog shows)."""
    return metadata_of(records(item)[0])


def metadata_of(record):
    source = _source_feature(record)
    qualifiers = source.qualifiers if source else {}

    def first(key):
        return str(qualifiers[key][0]) if qualifiers.get(key) else ""

    organism = record.annotations.get("organism") or first("organism") or ""
    references = [{"authors": r.authors, "title": r.title, "journal": r.journal,
                   "pubmed_id": r.pubmed_id} for r in record.annotations.get("references", [])]
    return {"description": own_description(record.description, record.name, record.id),
            "organism": "" if organism == "." else organism,
            "accession": "" if record.id.startswith("urn.local") else record.id,
            "strain": first("strain"), "isolate": first("isolate"),
            "country": first("country") or first("geo_loc_name"),
            "collection_date": first("collection_date"), "host": first("host"), "organelle": first("organelle"),
            **{k: first(k) for k in ("specimen_voucher", "isolation_source", "lat_lon", "collected_by", "identified_by",
                                     "culture_collection", "bio_material")},
            "taxonomy": record.annotations.get("taxonomy", []), "date": record.annotations.get("date", ""),
            "source_qualifiers": {k: v for k, v in qualifiers.items()}, "references": references}


def apply_record_metadata(record, fields):
    """Apply supplied metadata fields to a record without writing it."""
    if "description" in fields:
        record.description = (fields.get("description") or "").strip()
    organism = (fields.get("organism") or "").strip()
    if organism:
        record.annotations["organism"] = organism
    accession = (fields.get("accession") or "").strip()
    if accession:
        record.id = accession
        record.annotations["accessions"] = [re.sub(r"\.\d+$", "", accession)]  # ACCESSION, VERSION is the id
    source = _source_feature(record)
    wanted = {key: (fields.get(key) or "").strip() for key in metatable.SOURCE_KEYS if key in fields}
    if source is None and any(wanted.values()):
        # A FASTA import may have no source feature: make one (the editor and the
        # viewer skip it, and Export needs it for SOURCE and /mol_type)
        source = SeqFeature(FeatureLocation(0, len(record)), type="source")
        if organism or record.annotations.get("organism"):
            source.qualifiers["organism"] = [organism or record.annotations["organism"]]
        unit = record.annotations.get("molecule_type", "DNA")
        source.qualifiers["mol_type"] = [{"DNA": "genomic DNA", "RNA": "genomic RNA",
                                          "protein": "protein"}.get(unit, "genomic DNA")]
        record.features.insert(0, source)
    if source is not None:
        for key, value in wanted.items():
            if key == "organism" and not value:
                continue  # a record without an organism stays as it was
            if value:
                actual = "geo_loc_name" if key == "country" and "geo_loc_name" in source.qualifiers and "country" not in source.qualifiers else key
                source.qualifiers[actual] = [value]
            else:
                source.qualifiers.pop(key, None)
                if key == "country":
                    source.qualifiers.pop("geo_loc_name", None)
    return record


def _key(text):
    """A name as the table and the library compare it: case and spaces (GenBank names have none) ignored."""
    return re.sub(r"\s+", "_", str(text).strip().lower())


def _keys(text):
    key = _key(text)
    return {key, key.split(".")[0]} - {""}  # accessions with or without the version


def _members(item_ids):
    """The sequence items the ids stand for (a list: its sequences)."""
    out = []
    for item in [get_item(i) for i in item_ids]:
        out += [m for m in children(item) if m["kind"] == "sequence"]
    return list({m["id"]: m for m in out}.values())


def table_matches(item_ids, columns, rows):
    """For each column of a table: how many of these sequences have a name found in it (to
    guess which column holds the names)."""
    names = [{k for n in _names(m) for k in _keys(n)} for m in _members(item_ids)]
    counts = []
    for c in range(len(columns)):
        cells = set().union(*(_keys(row[c]) for row in rows)) if rows else set()
        counts.append(sum(1 for n in names if n & cells))
    return counts


@writes
def import_table_metadata(item_ids, rows, name_col, mapping, clear_empty=False, apply=False, expected_hash=None):
    """Metadata from a table's rows onto the sequences they name. mapping: {field: column};
    a cell fills its field (an empty cell leaves it alone, or empties it with clear_empty).
    apply False: only the report (matched, what would change for the first few).
    Returns {matched, changed (sequences), values (fields changed), unmatched (sequence labels),
    unused (table names matching nothing), duplicates, sample, applied}."""
    index, duplicates = {}, []
    for n, row in enumerate(rows):
        if not _key(row[name_col]):
            continue
        for k in _keys(row[name_col]):
            index.setdefault(k, set()).add(n)
    duplicates = sorted({rows[n][name_col] for ns in index.values() if len(ns) > 1 for n in ns})
    matched, unmatched, ambiguous, used = [], [], [], set()
    members = _members(item_ids)
    hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in {m["path"] for m in members}}
    digest = hashlib.sha256(json.dumps([sorted(m["id"] for m in members), hashes, rows, name_col,
                                       mapping, clear_empty], sort_keys=True).encode()).hexdigest()
    if apply and expected_hash is not None and expected_hash != digest:
        raise ValueError("The sequences or options changed since the preview; preview again")
    for m in members:
        hits = set().union(*(index.get(k, set()) for name in {m["name"], *_names(m)} for k in _keys(name)))
        if len(hits) > 1:
            ambiguous.append(m["name"])
        elif not hits:
            unmatched.append(m["name"])
        else:
            hit = next(iter(hits))
            matched.append((m, hit))
            used.add(hit)
    repeated_targets = Counter(hit for m, hit in matched)
    ambiguous.extend(m["name"] for m, hit in matched if repeated_targets[hit] > 1)
    matched = [(m, hit) for m, hit in matched if repeated_targets[hit] == 1]
    report = {"matched": len(matched), "changed": 0, "values": 0, "unmatched": unmatched[:30], "unmatched_count": len(unmatched),
              "unused": [rows[n][name_col] for n in range(len(rows)) if n not in used and rows[n][name_col]][:30],
              "duplicates": duplicates[:30], "ambiguous": ambiguous[:30], "ambiguous_count": len(ambiguous),
              "preview_hash": digest, "sample": [], "applied": apply}
    if apply and ambiguous:
        raise ValueError("Ambiguous table matches: " + ", ".join(ambiguous[:5]) + "; make the names unique before importing")
    lists, singles = {}, []  # plan all edits before writing any of them
    for number, (m, hit) in enumerate(matched):
        row = rows[hit]
        wanted = {f: row[c] for f, c in mapping.items() if row[c] or clear_empty}
        record = records(m)[0]
        before = metadata_of(record)
        changes = {f: v for f, v in wanted.items() if v != before.get(f, "") and not (f == "organism" and not v)}
        if not changes:
            continue
        report["changed"] += 1
        report["values"] += len(changes)
        if len(report["sample"]) < 6:
            report["sample"].append({"label": m["name"], "row": row[name_col],
                                     "changes": [[f, before.get(f, ""), v] for f, v in changes.items()]})
        if apply:
            apply_record_metadata(record, changes)
            if m["parent"]:
                lists.setdefault(m["parent"], {})[m["record"]] = (m, record)
            else:
                singles.append((m, record))
    # Checkpoint every baseline before opening rollback guards. No-op saves leave
    # these intact; a failure in any later file rolls the complete batch back.
    guards, whole_lists = [], set()
    try:
        for m, record in singles:
            owner = m["id"]
            history.checkpoint(owner, m["path"])
            guards.append((Path(m["path"]), [owner]))
        for parent, changes in lists.items():
            item = get_item(parent)
            path = Path(item["path"])
            owner = parent if path.stat().st_size <= BIG_LIST else None
            if owner:
                history.checkpoint(owner, path)
                owners = [owner]
                whole_lists.add(parent)
            else:
                owners = []
                for m, record in changes.values():
                    record_owner = _record_history(m)
                    history.checkpoint_bytes(record_owner, _record_bytes(m))
                    owners.append(record_owner)
            guards.append((path, owners))
        with ExitStack() as stack:
            for path, owners in guards:
                stack.enter_context(history.protect(path, owners))
            for m, record in singles:
                save_record(m, record)
            for parent, changes in lists.items():
                path = Path(get_item(parent)["path"])
                _splice(path, {n: record for n, (m, record) in changes.items()})
                if parent in whole_lists:
                    history.checkpoint(parent, path)
                else:
                    for m, record in changes.values():
                        history.checkpoint_bytes(_record_history(m), _record_bytes(get_item(m["id"])))
    except OSError as exc:
        note = "Inspect the retained recovery files before retrying." if "rollback failed" in str(exc) else "Metadata changes were rolled back."
        raise ValueError(f"Metadata import stopped: {exc}. {note}") from exc
    return report


@writes
def edit_metadata(item, fields):
    """Update a sequence record's metadata, preserving other records in a list."""
    if item["kind"] != "sequence":
        raise ValueError("Only a sequence record has metadata")
    record = apply_record_metadata(records(item)[0], fields)
    save_record(item, record)
    return record_metadata(item)


def rename(item, new_name):
    """A new name for an item. A sequence's record is renamed too (LOCUS name, and
    the id when it was the name, as for FASTA imports), so the name goes with it on
    export; lists, alignments and trees only change their name in the list."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("A name can't be empty")
    if item["kind"] == "sequence":
        record = records(item)[0]
        record_name = re.sub(r"\s+", "_", new_name)  # GenBank names have no spaces
        if record.id in (record.name, "<unknown id>"):
            record.id = record_name
        record.name = record_name
        save_record(item, record)
    if not item["parent"]:
        entries = load()
        for entry in entries:
            if entry["id"] == item["id"]:
                entry["name"] = new_name
        _save(entries)


def _alignment_records_for_rows(item, rows):
    """Match rows to current or saved records, preserving their metadata."""
    path = Path(item["path"])
    if not rows:
        raise ValueError("An alignment must contain at least one sequence")
    bases = lambda s: s.replace("-", "").replace(".", "").upper()
    def matched(records):
        remaining = iter(records)
        chosen = []
        for row in rows:
            match = next((r for r in remaining if r.id == row["name"] and bases(str(r.seq)) == bases(row["sequence"])), None)
            if match is None:
                return None
            chosen.append(match)
        return chosen
    records = matched(read_records(path, "fasta"))
    if records is None and item.get("id"):
        for version in reversed(history.versions(item["id"])["versions"]):
            try:
                compressed = history.directory(item["id"]) / (version["hash"] + ".gz")
                raw = gzip.decompress(compressed.read_bytes())
                if hashlib.sha256(raw).hexdigest() != version["hash"]:
                    continue
                text = raw.decode("utf-8")
                records = matched(read_records(StringIO(text), "fasta"))
                if records is not None:
                    break
            except (OSError, UnicodeError, ValueError):
                continue
    if records is None:
        raise ValueError("Rows must retain their original order and bases; only gaps or saved rows may change")
    return records


@writes
def save_alignment(item, rows):
    """Save gap edits and row removals; previously saved rows can be restored."""
    path = Path(item["path"])
    records = _alignment_records_for_rows(item, rows)
    for record, row in zip(records, rows):
        record.seq = Seq(row["sequence"])
    staging = path.with_name(path.name + ".saving")
    write_fasta(records, staging)
    if item.get("id"):
        history.checkpoint(item["id"], path)
    with history.protect(path, [item["id"]] if item.get("id") else []):
        replace_file(staging, path)
        if item.get("id"):
            history.checkpoint(item["id"], path)


FILE_FIELDS = ("file", "bam", "xmfa", "figure", "variants", "trace", "reads", "hits", "tracks")


def _files_of(entry):
    """An entry's files in the library folder: its own and the ones beside it."""
    return ([entry[k] for k in FILE_FIELDS if entry.get(k)] + ([f"{entry['bam']}.bai"] if entry.get("bam") else [])
            + list(entry.get("read_files") or []))  # a reads item's FASTQ files


def check_file_content(entry, folder):
    """Read an item's primary format and JSON sidecars; shared by integrity and ZIP import."""
    path, kind = folder / entry["file"], entry["kind"]
    if kind in ("sequence", "list", "alignment"):
        records = read_records(path, "fasta" if kind == "alignment" else "genbank")
        if not records:
            raise ValueError("No records found")
        if kind == "alignment" and len({len(r) for r in records}) != 1:
            raise ValueError("Alignment rows have different lengths")
    elif kind == "graph":
        graph.read_gfa(path.read_text(encoding="utf-8"))
    elif kind == "tree":
        Phylo.read(StringIO(path.read_text(encoding="utf-8")), "newick")
    elif kind == "variants":
        if not path.read_text(encoding="utf-8").startswith("##fileformat=VCF"):
            raise ValueError("Not a VCF file")
    elif kind in ("mapping", "comparison", "blast", "primers", "figure", "reads"):
        if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
            raise ValueError("Expected a JSON object")
    for key in ("figure", "trace", "hits", "tracks"):
        if entry.get(key):
            if not isinstance(json.loads((folder / entry[key]).read_text(encoding="utf-8")), dict):
                raise ValueError(f"Expected a JSON object in {key}")
    if entry.get("variants"):
        if kind != "alignment":
            raise ValueError("Only alignments can have an attached VCF")
        text = (folder / entry["variants"]).read_text(encoding="utf-8")
        if not text.startswith("##fileformat=VCFv4.") or not any(
                line.startswith("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO")
                for line in text.splitlines()):
            raise ValueError("Invalid attached VCF header")


@writes
def check_integrity():
    """Inspect registered files without changing the library or its contents."""
    entries = load()
    referenced, issues = set(), []
    for entry in entries:
        item_id, name, kind = entry.get("id"), entry.get("name", "(unnamed)"), entry.get("kind")
        for filename in _files_of(entry):
            if not isinstance(filename, str) or Path(filename).name != filename:
                issues.append({"item": item_id, "name": name, "file": str(filename), "problem": "Invalid library filename"})
                continue
            referenced.add(filename)
            path = library_dir() / filename
            if not path.is_file():
                issues.append({"item": item_id, "name": name, "file": filename, "problem": "File is missing"})
            elif filename in (entry.get("figure"), entry.get("trace"), entry.get("tracks")):
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    issues.append({"item": item_id, "name": name, "file": filename, "problem": f"Can't read the file beside it: {exc}"})
        filename = entry.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            continue
        path = library_dir() / filename
        if not path.is_file():
            continue
        try:
            check_file_content(entry, library_dir())
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError) as exc:
            issues.append({"item": item_id, "name": name, "file": filename, "problem": f"Can't read the item: {exc}"})
    unreferenced = sorted(p.name for p in library_dir().iterdir() if p.is_file() and p.name not in referenced)
    return {"checked": len(entries), "issues": issues, "unreferenced": unreferenced}


@writes
def remove(item_ids, trash=False):
    """Deletes items: their files in the library, or records from a list.
    trash: kept in the trash instead (the Delete command; the moves that remove
    what they copied elsewhere, as Group, don't)."""
    targets = [it for it in items() if it["id"] in item_ids]
    whole = {it["id"] for it in targets if not it["parent"]}
    entries = load()

    # Records taken out of lists (unless the whole list goes)
    by_list = {}
    for it in targets:
        if it["parent"] and it["parent"] not in whole:
            by_list.setdefault(it["parent"], []).append(it)
    for list_id, members in by_list.items():
        entry = next(e for e in entries if e["id"] == list_id)
        path = library_dir() / entry["file"]
        if len({m["record"] for m in members}) < len(record_index(path)):
            if trash:
                _trash_members(entry, members)
            _splice(path, {m["record"]: None for m in members})
        else:
            whole.add(list_id)

    for entry in [e for e in entries if e["id"] in whole]:
        if trash:
            _trash_entry(entry)
        else:
            for name in _files_of(entry):
                when_free(lambda: (library_dir() / name).unlink(missing_ok=True))
        entries.remove(entry)
    _save(entries)


# ── The trash ────────────────────────────────────────────────────────────
# Deleted items wait in .cladia/trash/<id>/: their files as they were, and
# item.json (the list entry, with its folder and the files beside it, when it was
# deleted). Sequences deleted from a list are kept together as one item, which
# comes back beside the list (as a sequence, or a list of them), not into it.
# Settings > Storage lists them, restores them, or deletes them for good.

def trash_dir():
    return config.APP_DIR / "trash"


def _trash_folder(meta):
    folder = trash_dir() / meta["id"]
    folder.mkdir(parents=True)
    return folder


def _trash_entry(entry):
    meta = {"id": uuid.uuid4().hex[:12], "deleted": time.time(), "label": entry["name"], "kind": entry["kind"],
            "entry": entry}
    folder = _trash_folder(meta)
    size = 0
    for name in _files_of(entry):
        source = library_dir() / name
        if source.exists():
            size += source.stat().st_size
            when_free(lambda: shutil.move(str(source), str(folder / name)))
    for key, name in ((entry["id"], "history"), (f"{entry['id']}_figure", "figure-history"),
                      (f"{entry['id']}_variants", "variants-history")):
        saved_history = history.directory(key)
        if saved_history.is_dir():
            size += sum(p.stat().st_size for p in saved_history.rglob("*") if p.is_file())
            shutil.move(str(saved_history), str(folder / name))
    (folder / "item.json").write_text(json.dumps({**meta, "size": size}), encoding="utf-8")


def _trash_members(list_entry, members):
    """Sequences deleted from a list: their records' bytes, as one item."""
    one = len(members) == 1
    name = members[0]["name"] if one else f"{list_entry['name']} (deleted sequences)"
    meta = {"id": uuid.uuid4().hex[:12], "deleted": time.time(), "label": name, "kind": "sequence" if one else "list",
            "from_list": list_entry["name"], "from_id": list_entry["id"]}
    folder = _trash_folder(meta)
    file = f"{safe_name(name)}.gb"
    (folder / file).write_bytes(b"".join(_record_bytes(m) for m in members))
    entry = {"id": meta["id"], "file": file, "kind": meta["kind"], "name": name,
             **({"folder": list_entry["folder"]} if list_entry.get("folder") else {})}
    (folder / "item.json").write_text(json.dumps({**meta, "entry": entry, "size": (folder / file).stat().st_size}),
                                      encoding="utf-8")


def trash_items():
    """What's in the trash, newest first: {id, label, kind, deleted, size, folder, from_list}."""
    found = []
    for meta_file in trash_dir().glob("*/item.json") if trash_dir().is_dir() else []:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append({k: meta.get(k) for k in ("id", "label", "kind", "deleted", "size", "from_list")}
                     | {"folder": meta["entry"].get("folder")})
    return sorted(found, key=lambda m: -(m["deleted"] or 0))


def _free_name(name):
    """name, or with _2, _3... before its first dot (x.compare.json -> x_2.compare.json)
    if the library has one by that name."""
    stem, dot, rest = name.partition(".")
    candidate, n = name, 2
    while (library_dir() / candidate).exists():
        candidate, n = f"{stem}_{n}{dot}{rest}", n + 1
    return candidate


@writes
def restore(trash_ids):
    """Trash items back in the library, in the folder they were in; returns their ids."""
    entries, restored = load(), []
    used = {e["id"] for e in entries}
    moved, completed = [], []

    def move(source, target):
        shutil.move(str(source), str(target))
        moved.append((source, target))

    try:
        for tid in trash_ids:
            folder = trash_dir() / tid
            meta = json.loads((folder / "item.json").read_text(encoding="utf-8"))
            entry = dict(meta["entry"])
            if (entry["id"] in used or
                ((folder / "history").is_dir() and history.directory(entry["id"]).exists()) or
                ((folder / "figure-history").is_dir() and history.directory(f"{entry['id']}_figure").exists()) or
                ((folder / "variants-history").is_dir() and history.directory(f"{entry['id']}_variants").exists())):
                entry["id"] = uuid.uuid4().hex[:12]
            for key in FILE_FIELDS:
                if not entry.get(key) or not (folder / entry[key]).exists():
                    continue
                new = _free_name(entry[key])
                move(folder / entry[key], library_dir() / new)
                if key == "bam" and (folder / f"{entry[key]}.bai").exists():
                    move(folder / f"{entry[key]}.bai", library_dir() / f"{new}.bai")
                entry[key] = new
            if (folder / "history").is_dir():
                target_history = history.directory(entry["id"])
                target_history.parent.mkdir(parents=True, exist_ok=True)
                move(folder / "history", target_history)
            if (folder / "figure-history").is_dir():
                target_history = history.directory(f"{entry['id']}_figure")
                target_history.parent.mkdir(parents=True, exist_ok=True)
                move(folder / "figure-history", target_history)
            if (folder / "variants-history").is_dir():
                target_history = history.directory(f"{entry['id']}_variants")
                target_history.parent.mkdir(parents=True, exist_ok=True)
                move(folder / "variants-history", target_history)
            used.add(entry["id"])
            entries.append(entry)
            restored.append(entry["id"])
            completed.append(folder)
        _save(entries)
    except Exception as exc:
        errors = []
        for source, target in reversed(moved):
            try:
                shutil.move(str(target), str(source))
            except OSError as rollback_error:
                errors.append(str(rollback_error))
        if errors:
            raise OSError(f"Restore failed ({exc}); some files could not be returned to Trash: {'; '.join(errors)}") from exc
        raise
    for folder in completed:
        shutil.rmtree(folder, ignore_errors=True)
    return restored


@writes
def empty_trash(trash_ids=None):
    """Deletes trash items for good (None: all of them); returns how many."""
    ids = [m["id"] for m in trash_items()] if trash_ids is None else trash_ids
    for tid in ids:
        if re.fullmatch(r"[0-9a-f]{12}", tid):
            shutil.rmtree(trash_dir() / tid, ignore_errors=True)
    return len(ids)


def _record_bytes(item):
    """A sequence item's GenBank text as bytes (a list member's byte range)."""
    data = Path(item["path"]).read_bytes()
    if item["parent"]:
        start, end, _ = record_index(item["path"])[item["record"]]
        data = data[start:end]
    return data if data.endswith(b"\n") else data + b"\n"


@writes
def group(item_ids, name, remove_source=True, folder=DEFAULT_FOLDER):
    """Sequences (and lists: all their sequences) moved into one new sequence list,
    as if imported from one file; returns its id. The records are copied as they
    are (byte ranges), then the items they came from are removed."""
    chosen = [get_item(i) for i in item_ids]
    if any(it["kind"] not in ("sequence", "list") for it in chosen):
        raise ValueError("only sequences and sequence lists can be grouped")
    _no_reads(chosen)
    destination = result_folder(chosen, folder)
    members = []
    for it in chosen:
        for child in children(it):
            if (child["path"], child["record"]) not in [(m["path"], m["record"]) for m in members]:
                members.append(child)
    if len(members) < 2:
        raise ValueError("a list needs at least two sequences")
    target = unique_path(library_dir() / f"{safe_name(name)}.gb")
    target.write_bytes(b"".join(_record_bytes(m) for m in members))
    new_id = _register(target, "list", {"name": name.strip() or target.stem})
    move([new_id], destination)
    if remove_source:
        remove([it["id"] for it in chosen])
    return new_id


@writes
def add_to_group(list_id, item_ids):
    """Sequences (and lists: their sequences) moved into an existing list, at its
    end; ones already in it are left out. Returns how many went in."""
    target = get_item(list_id)
    chosen = [it for it in (get_item(i) for i in item_ids) if it["id"] != list_id and it["parent"] != list_id]
    if any(it["kind"] not in ("sequence", "list") for it in chosen):
        raise ValueError("only sequences and sequence lists can go into a list")
    _no_reads(chosen)
    members = []
    for it in chosen:
        for child in children(it):
            if (child["path"], child["record"]) not in [(m["path"], m["record"]) for m in members]:
                members.append(child)
    if not members:
        return 0
    data = b"".join(_record_bytes(m) for m in members)
    path = Path(target["path"])
    old = path.read_bytes()
    staging = path.with_name(path.name + ".saving")
    staging.write_bytes(old + (b"" if old.endswith(b"\n") else b"\n") + data)
    replace_file(staging, path)
    remove([it["id"] for it in chosen])
    return len(members)


@writes
def take_out(item_ids, folder=DEFAULT_FOLDER):
    """List members taken out of their lists: each a sequence of its own (its
    record's bytes as they are), beside their source lists unless `folder` is
    explicit (None means Library root). A list left empty goes. Returns new ids."""
    members = [it for it in (get_item(i) for i in item_ids) if it["parent"]]
    if folder is not DEFAULT_FOLDER:
        folder = result_folder([], folder)
    new = []
    for m in members:
        target = unique_path(library_dir() / f"{safe_name(m['name'])}.gb")
        target.write_bytes(_record_bytes(m))
        destination = folder_of(m) if folder is DEFAULT_FOLDER else folder
        new.append(_register(target, "sequence", {"name": m["name"], **({"folder": destination} if destination else {})}))
    remove([m["id"] for m in members])
    return new


SORT_KEYS = {"name": False, "length": True, "organism": False, "description": False, "distance": False}  # key -> descending first


def _distance_order(rows, reference, descending):
    """Uncorrected mismatch proportion at comparable aligned sites; missing
    sites are excluded pairwise. Rows with no comparable sites stay last."""
    if reference is None or not 0 <= reference < len(rows):
        raise ValueError("Select one sequence in the alignment to sort by distance")
    protein = looks_protein("".join(r["sequence"][:5000] for r in rows[:20]))
    alphabet = set("ACDEFGHIKLMNPQRSTVWYOU" if protein else "ACGT")
    sequences = [r["sequence"].upper() for r in rows]
    if not protein:
        sequences = [seq.replace("U", "T") for seq in sequences]
    ref = sequences[reference]
    distances = {}
    for i, seq in enumerate(sequences):
        comparable = differences = 0
        for a, b in zip(ref, seq):
            if a in alphabet and b in alphabet:
                comparable += 1
                differences += a != b
        if comparable:
            distances[i] = differences / comparable
    # Keep ties in their current order and the reference first among zeroes.
    ordered = sorted((i for i in distances if i != reference), key=lambda i: distances[i], reverse=descending)
    if reference in distances:
        ordered.insert(len(ordered) if descending else 0, reference)
    return ordered + [i for i in range(len(rows)) if i not in distances]


def _order(facts, key, descending):
    """Indices of facts ({name, length, organism, description}) in order; ones
    without an organism or description go last either way, by name."""
    has = [i for i, f in enumerate(facts) if key in ("name", "length") or f[key]]
    value = lambda i: facts[i][key] if key == "length" else facts[i][key].lower()
    has.sort(key=lambda i: (value(i), facts[i]["name"].lower()), reverse=descending)
    rest = sorted(set(range(len(facts))) - set(has), key=lambda i: facts[i]["name"].lower())
    return has + rest


@writes
def sort_item(item_id, key, descending=None, reference=None):
    """Reorders a list's records (byte ranges, nothing parsed) or an alignment's
    rows (length: bases without gaps) in the file; the order is remembered on the
    entry ("sorted") so the menu can show it. descending None: the key's usual way."""
    item = get_item(item_id)
    if key == "distance" and item["kind"] != "alignment":
        raise ValueError("Distance sorting needs an alignment")
    descending = SORT_KEYS[key] if descending is None else descending
    path = Path(item["path"])
    staging = path.with_name(path.name + ".saving")
    if item["kind"] == "alignment":
        rows = alignment_rows(item)
        for r in rows:
            r["length"] = len(r["sequence"].replace("-", ""))
        records = read_records(path, "fasta")
        order = _distance_order(rows, reference, descending) if key == "distance" else _order(rows, key, descending)
        write_fasta([records[i] for i in order], staging)
    else:
        index, metas = record_index(path), record_meta(path)
        data = path.read_bytes()
        pieces = [data[index[i][0]:index[i][1]] for i in _order(metas, key, descending)]
        staging.write_bytes(b"".join(p if p.endswith(b"\n") else p + b"\n" for p in pieces))
    replace_file(staging, path)
    entries = load()
    for e in entries:
        if e["id"] == item["id"]:
            e["sorted"] = {"key": key, "descending": descending}
            if key == "distance":
                e["sorted"]["reference"] = rows[reference]["name"]
    _save(entries)


@writes
def split(list_id, remove_source=True):
    """Turns a sequence list into one sequence item per record; returns their ids."""
    item = get_item(list_id)
    ids = []
    for record in read_records(Path(item["path"]), "genbank"):
        target = unique_path(library_dir() / f"{safe_name(record.name)}.gb")
        write_record(record, target)
        ids.append(same_folder(_register(target, "sequence", {"name": record.name}), item))
    if remove_source:
        remove([list_id])
    return ids


# ── Export ───────────────────────────────────────────────────────────────

# ── Alignments: where the rows came from ────────────────────────────────
# An alignment entry may have "sources": row name -> {"item": library item id,
# "offset": bases of that sequence before the alignment's first column}. MAFFT
# jobs record them; Extract keeps them (with a new offset). They let the
# alignment view show each row's features. MAFFT's "_R_" prefix (a reversed
# row) is not part of the name stored.

def row_source(item, row_name):
    """(source item id or None, offset, reversed, turn) for one row of an alignment;
    turn: the source sequence is first turned to start there (a block of a
    comparison whose genomes were lined up)."""
    reversed_ = row_name.startswith("_R_")
    base = row_name[3:] if reversed_ else row_name
    src = (item.get("sources") or {}).get(base) or {}
    if isinstance(src, str):
        src = {"item": src}
    return src.get("item"), src.get("offset", 0), reversed_, src.get("turn", 0)


def extract_alignment(item, start, end, rows=None):
    """A new alignment from columns start..end (0-based, inclusive) of rows r0..r1."""
    recs = read_records(Path(item["path"]), "fasta")
    if rows:
        recs = recs[rows[0]: rows[1] + 1]
    pieces, sources = [], {}
    for r in recs:
        piece = r[start: end + 1]
        piece.id, piece.description = r.id, ""
        pieces.append(piece)
        source, offset, _, turn = row_source(item, r.id)
        before = len(str(r.seq[:start]).replace("-", ""))
        base = r.id[3:] if r.id.startswith("_R_") else r.id
        sources[base] = {**({"item": source} if source else {}), "offset": offset + before,
                         **({"turn": turn} if turn else {})}
    name = f"{item['name']}_{start + 1}-{end + 1}"
    target = unique_path(library_dir() / f"{safe_name(name)}.fasta")
    write_fasta(pieces, target)
    return same_folder(_register(target, "alignment", {"name": name, "sources": sources}), item)


@writes
def copy_alignment_rows(item, rows, as_sequences=False):
    """Copy selected rows as an alignment or a list of ungapped sequences."""
    matched_records = _alignment_records_for_rows(item, rows)
    if not as_sequences:
        if len(rows) < 2:
            raise ValueError("Select at least two rows for a new alignment")
        width = max(len(row["sequence"]) for row in rows)
        seqs = [row["sequence"].ljust(width, "-") for row in rows]
        start, end = 0, width
        while start < end and all(s[start] in "-." for s in seqs):
            start += 1
        while end > start and all(s[end - 1] in "-." for s in seqs):
            end -= 1
        for record, seq in zip(matched_records, seqs):
            record.seq = Seq(seq[start:end])
        source_keys = {r.id[3:] if r.id.startswith("_R_") else r.id for r in matched_records}
        sources = {k: v for k, v in (item.get("sources") or {}).items() if k in source_keys}
        name = f"{item['name']}_rows"
        target = unique_path(library_dir() / f"{safe_name(name)}.fasta")
        write_fasta(matched_records, target)
        return [same_folder(_register(target, "alignment", {"name": name, "sources": sources}), item)]

    row_facts = {r["name"]: r for r in alignment_rows(item)}
    if any(not row["sequence"].replace("-", "").replace(".", "") for row in rows):
        raise ValueError("A selected row contains only gaps")
    copied = []
    for record, row in zip(matched_records, rows):
        sequence = row["sequence"].replace("-", "").replace(".", "")
        source_id, offset, reversed_, turn = row_source(item, record.id)
        original = None
        if source_id and not offset and not reversed_ and not turn:
            try:
                source = get_item(source_id)
                candidate = records(source)[0]
                if str(candidate.seq).upper() == sequence.upper():
                    original = candidate
            except (KeyError, OSError, IndexError, ValueError):
                pass
        if original is not None:
            record = original
        else:
            record.seq = Seq(sequence)
            record.annotations["molecule_type"] = "protein" if looks_protein(sequence) else "DNA"
            organism = row_facts.get(row["name"], {}).get("organism")
            if organism:
                record.annotations["organism"] = organism
        record.name = re.sub(r"\s+", "_", row["name"].strip()) or "sequence"
        record.id = record.name
        copied.append(record)
    name = f"{item['name']} sequences"
    target = unique_path(library_dir() / f"{safe_name(item['name'])}_sequences.gb")
    write_record(copied, target)
    return [same_folder(_register(target, "list", {"name": name}), item)]


def tree_nodes(item):
    """A tree item for the tree viewer: its nodes, parents before children, each
    {name, length, support, parent} (parent: index, -1 for the root). Numeric
    internal labels are supports (IQ-TREE's bootstrap); other internal labels,
    e.g. "80/95" (SH-aLRT/UFBoot), are kept as text in `support` too."""
    tree = Phylo.read(StringIO(Path(item["path"]).read_text(encoding="utf-8")), "newick")
    nodes, stack = [], [(tree.root, -1)]
    while stack:  # no recursion: a ladder-like tree of thousands of tips is deep
        clade, parent = stack.pop()
        tip = clade.is_terminal()
        support = clade.confidence if clade.confidence is not None else (None if tip else clade.name)
        if isinstance(support, float) and support.is_integer():
            support = int(support)
        nodes.append({"name": clade.name if tip else None, "length": clade.branch_length,
                      "support": None if support is None else str(support), "parent": parent})
        here = len(nodes) - 1
        stack += [(child, here) for child in reversed(clade.clades)]
    return nodes


@writes
def reroot_tree(item, outgroup):
    """Roots a tree item on the branch above the clade of the `outgroup` tips (the
    viewer's reroot) and saves it. Support values belong to branches (splits of
    the tips in two), so each is looked up by its split after rerooting; moving
    node labels along, as viewers do, would put them on the wrong branches."""
    path = Path(item["path"])
    tree = Phylo.read(StringIO(path.read_text(encoding="utf-8")), "newick")
    tips = frozenset(t.name for t in tree.get_terminals())
    outgroup = frozenset(outgroup) & tips
    if not outgroup or outgroup == tips:
        raise ValueError("Pick a clade to root on")
    first = min(tips)

    def side_of(clade):  # a split, named by its side without the first tip
        names = frozenset(t.name for t in clade.get_terminals())
        return names if first not in names else tips - names

    support = {side_of(c): c.confidence for c in tree.get_nonterminals()
               if c.confidence is not None and c is not tree.root}
    # The outgroup may span the current root: then its complement is a clade
    group = outgroup if tree.is_monophyletic([t for t in tree.get_terminals() if t.name in outgroup]) else tips - outgroup
    targets = [t for t in tree.get_terminals() if t.name in group]
    half = (tree.common_ancestor(targets).branch_length or 0) / 2  # the root goes halfway along its branch
    tree.root_with_outgroup(targets, outgroup_branch_length=half)
    for clade in tree.get_nonterminals():
        clade.name = None
        clade.confidence = None if clade is tree.root else support.get(side_of(clade))
    if len(tree.root.clades) == 2:
        # The root's two branches are halves of one branch: its support shows once
        a, b = tree.root.clades
        a.branch_length = b.branch_length = half
        tree.common_ancestor(targets).confidence = None
    buffer = StringIO()
    Phylo.write(tree, buffer, "newick", format_branch_length="%g", format_confidence="%g")
    previous = path.read_text(encoding="utf-8")
    staging = path.with_name(path.name + ".saving")
    staging.write_text(buffer.getvalue(), encoding="utf-8")
    if item.get("id"):
        history.checkpoint(item["id"], path)
    with history.protect(path, [item["id"]] if item.get("id") else []):
        replace_file(staging, path)
        if item.get("id"):
            history.checkpoint(item["id"], path)
    return previous  # (the viewer keeps it: Ctrl+Z writes it back, set_tree_text)


@writes
def prune_tree(item, tips):
    """Removes tips from a tree item and saves it (its saved versions undo it).
    A node left with one child goes, its branch joined to the child's; supports
    stay with the clades that keep them."""
    path = Path(item["path"])
    tree = Phylo.read(StringIO(path.read_text(encoding="utf-8")), "newick")
    terminals = tree.get_terminals()
    gone = set(tips)
    targets = [tip for tip in terminals if tip.name in gone]
    if not targets:
        raise ValueError("Select the tips to remove")
    if len(terminals) - len(targets) < 3:
        raise ValueError("A tree keeps at least three tips")
    for tip in targets:
        tree.prune(tip)
    staging = path.with_name(path.name + ".saving")
    staging.write_text(formats.newick_text(tree), encoding="utf-8")
    history.checkpoint(item["id"], path)
    with history.protect(path, [item["id"]]):
        replace_file(staging, path)
        history.checkpoint(item["id"], path)
    return len(targets)


@writes
def set_tree_text(item, text):
    """A tree item's file written back as it was (undoing a reroot, or redoing
    it); returns the text it replaces."""
    try:
        tips = Phylo.read(StringIO(text), "newick").count_terminals()
    except Exception as e:
        raise ValueError(f"not a readable Newick tree ({e})")
    if tips < 2:  # (Bio.Phylo reads any word as a one-tip tree)
        raise ValueError("not a Newick tree")
    path = Path(item["path"])
    previous = path.read_text(encoding="utf-8")
    staging = path.with_name(path.name + ".saving")
    staging.write_text(text, encoding="utf-8")
    history.checkpoint(item["id"], path)
    with history.protect(path, [item["id"]]):
        replace_file(staging, path)
        history.checkpoint(item["id"], path)
    return previous


def export_trees(item_list, fmt):
    """The trees of the given items as Newick (one per line) or NEXUS."""
    return formats.write_trees([Path(it["path"]).read_text(encoding="utf-8") for it in item_list], fmt)


def export_alignment(item, fmt):
    """An alignment as NEXUS or PHYLIP."""
    recs = records(item)
    return formats.write_alignment(recs, fmt, looks_protein("".join(str(r.seq) for r in recs[:5])))


def export_text(item_list, fmt, codes=None):
    """The records of the given items as one GenBank or FASTA text.

    fmt "protein": the proteins of all CDSs, as FASTA. codes: item id -> the
    genetic code picked in the editor (else the one from the file).
    """
    seen, recs = set(), []
    for item in item_list:
        for child in ([item] if item["kind"] == "alignment" else children(item)):
            key = (child["path"], child["record"])
            if key not in seen:  # a list and one of its sequences both picked
                seen.add(key)
                if child["kind"] == "alignment":
                    recs += records(child)
                elif child["kind"] == "mapping":  # its consensus
                    recs.append(consensus_record(child)[0])
                elif fmt == "protein":
                    recs += _proteins(child, (codes or {}).get(child["id"]))
                else:
                    recs.append(records(child)[0])
    if fmt in ("gff3", "gff3fasta"):  # features only, or with the sequences (##FASTA)
        return gff.records_to_gff3(recs, with_fasta=fmt == "gff3fasta")
    buffer = StringIO()
    SeqIO.write(recs, buffer, "fasta" if fmt == "protein" else fmt)
    return buffer.getvalue()


def export_graph(item, fmt):
    """An assembly graph as its GFA file, or its nodes as FASTA."""
    if fmt == "gfa":
        return decode(Path(item["path"]).read_bytes())
    nodes = graph_data(item)["nodes"]
    return "".join(f">node_{name} length={n['length']}" + (f" depth={n['depth']:.1f}" if n["depth"] is not None else "")
                   + f"\n{n['seq']}\n" for name, n in nodes.items() if n["seq"])


def _proteins(item, code):
    """One protein record per CDS: >name sequence location"""
    record = records(item)[0]
    code = code or genetic_code(record)
    result = []
    for f in record.features:
        if f.type != "CDS":
            continue
        name = feature_name(f.qualifiers, "CDS")
        where = f"{int(f.location.start) + 1}-{int(f.location.end)}{'(-)' if f.location.strand == -1 else ''}"
        result.append(SeqRecord(Seq(cds_protein(f, record.seq, code)), id=safe_name(name),
                                description=f"{item['name']} {where}"))
    return result


# ── Names ────────────────────────────────────────────────────────────────

def unique_path(path):
    """path, or path with _2, _3... appended to the stem if it already exists."""
    path = Path(path)
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        n += 1
    return candidate


def safe_name(text):
    """Text usable in a Windows file name."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "sequence"
