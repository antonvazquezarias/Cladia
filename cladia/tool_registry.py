"""Core tool categories and operations, with the engine modules in tool_plugins/.

An engine module exports ``ENGINE`` and ``run``. It supplies data and a runner,
never UI markup; a broken one is logged and left out. An optional engine
(``ENGINE["optional"]``) also has ``install`` and ``uninstall``: it shows in the
tool dialogs once installed in Settings › Tools, and most download their tool
from bioconda then (``ENGINE["conda"]``, conda.py).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from . import config

log = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).with_name("tool_plugins")
OPTION_TYPES = {"int", "float", "bool", "choice", "file", "text"}


class Registry:
    def __init__(self, installed=None):
        self.categories = {}
        self.operations = {}
        self.engines = {}
        self.installed = installed if installed is not None else installed_engines()

    def registerCategory(self, entry):
        self._register(self.categories, entry, {"id", "label", "icon", "order"})

    def registerOperation(self, entry):
        self._register(self.operations, entry, {"id", "category", "label", "description", "inputs", "order"})
        for slot in entry["inputs"]:
            minimum = slot.get("minRows")
            maximum = slot.get("maxRows")
            if (minimum is not None and (type(minimum) is not int or minimum < 1) or
                    maximum is not None and (type(maximum) is not int or maximum < 1) or
                    minimum is not None and maximum is not None and minimum > maximum):
                del self.operations[entry["id"]]
                raise ValueError("Invalid alignment row limits")
        if entry["category"] not in self.categories:
            del self.operations[entry["id"]]
            raise ValueError(f"Unknown category: {entry['category']}")

    def registerEngine(self, entry, run, install=None, uninstall=None):
        if not callable(run):
            raise ValueError("Engine has no callable run function")
        self._validate_engine(entry)
        if entry.get("optional") and not (callable(install) and callable(uninstall)):
            raise ValueError("Optional engine has no callable install and uninstall functions")
        self._register(self.engines, {**entry, "run": run, "install": install, "uninstall": uninstall},
                       {"id", "label", "blurb", "supports", "options", "lane", "cores", "run"})

    @staticmethod
    def _register(table, entry, required):
        missing = required - entry.keys()
        if missing:
            raise ValueError(f"Missing fields: {', '.join(sorted(missing))}")
        if not isinstance(entry["id"], str) or not entry["id"]:
            raise ValueError("Expected a nonempty id")
        if entry["id"] in table:
            raise ValueError(f"Duplicate id: {entry['id']}")
        table[entry["id"]] = entry

    def _validate_engine(self, entry):
        if "image" in entry:  # its tool comes as a Docker image: an isolated root of its own
            from . import linux
            linux.add_environment(entry.get("id"), entry["image"])
        if "citations" in entry and not (isinstance(entry["citations"], (list, tuple)) and all(
                isinstance(c, (list, tuple)) and len(c) == 3 and all(isinstance(x, str) for x in c) for c in entry["citations"])):
            raise ValueError("Engine citations must be [tool, reference, url] entries")
        if "storage" in entry:
            from .tool_storage import validate
            validate(entry["storage"])
        if entry.get("lane") not in {"compute", "download", "blast", "pcr"} or (
                entry.get("cores") is not None and (not isinstance(entry["cores"], int) or entry["cores"] < 0)):
            raise ValueError("Engine has invalid lane or core requirement")
        if not isinstance(entry.get("supports"), (list, tuple)) or not entry["supports"]:
            raise ValueError("Engine supports must be a nonempty list")
        unknown = set(entry["supports"]) - self.operations.keys()
        if unknown:
            raise ValueError(f"Unknown operation ids: {', '.join(sorted(unknown))}")
        if not isinstance(entry.get("options"), (list, tuple)):
            raise ValueError("Engine options must be a list")
        seen = set()
        for option in entry["options"]:
            if not isinstance(option, dict) or not {"key", "label", "type", "default"} <= option.keys():
                raise ValueError("Bad option spec: key, label, type and default are required")
            key, kind = option["key"], option["type"]
            if not isinstance(key, str) or not key or key in seen:
                raise ValueError(f"Duplicate or empty option key: {key}")
            seen.add(key)
            if kind not in OPTION_TYPES:
                raise ValueError(f"Bad option type for {key}: {kind}")
            if kind == "choice":
                choices = option.get("choices")
                if not isinstance(choices, list) or not choices or any(
                        not isinstance(c, dict) or not {"value", "label"} <= c.keys() for c in choices):
                    raise ValueError(f"Bad choices for {key}")
                if option["default"] not in [c["value"] for c in choices]:
                    raise ValueError(f"Default not in choices for {key}")
            elif kind in ("int", "float"):
                default = option["default"]
                if default is not None and (isinstance(default, bool) or not isinstance(default, (int, float))):
                    raise ValueError(f"Bad numeric default for {key}")
                if option.get("min") is not None and option.get("max") is not None and option["min"] > option["max"]:
                    raise ValueError(f"Bad numeric range for {key}")
            elif kind == "bool" and not isinstance(option["default"], bool):
                raise ValueError(f"Bad boolean default for {key}")
            elif kind in ("file", "text") and not isinstance(option["default"], str):
                raise ValueError(f"Bad text default for {key}")
        if entry.get("optional"):
            choices = entry.get("installChoices", [])
            if not isinstance(choices, list) or any(not isinstance(c, dict) or
                    not {"value", "label"} <= c.keys() for c in choices):
                raise ValueError("Bad installation choices")
            settings = entry.get("settings", [])
            if not isinstance(settings, list) or any(not isinstance(field, dict) or
                    not {"key", "label", "type"} <= field.keys() or
                    field["type"] not in {"text", "secret", "bool"} or
                    not isinstance(field["key"], str) or not field["key"].isidentifier()
                    for field in settings):
                raise ValueError("Bad tool settings")
            if len({field["key"] for field in settings}) != len(settings):
                raise ValueError("Duplicate tool setting key")

    def operationsFor(self, category):
        return sorted((o for o in self.operations.values() if o["category"] == category and self.enginesFor(o["id"])),
                      key=lambda o: o["order"])

    def enginesFor(self, operation):
        return sorted((e for e in self.engines.values() if operation in e["supports"] and self.available(e)),
                      key=lambda e: e.get("order", 0))

    def available(self, engine):
        return not engine.get("optional") or engine["id"] in self.installed

    def installable(self, include_builtin=False):
        return [{"id": e["id"], "label": e.get("catalogLabel", e["label"]), "blurb": e["blurb"],
                 "type": self.categories[self.operations[e["supports"][0]]["category"]]["label"],
                 "typeOrder": self.categories[self.operations[e["supports"][0]]["category"]]["order"],
                 "installed": self.available(e), "builtin": not e.get("optional", False), "choices": e.get("installChoices", []),
                 "installedChoices": self.installed.get(e["id"], []), "settings": e.get("settings", []),
                 "estimateBytes": e.get("storage", {}).get("estimateBytes"),
                 "version": (e.get("conda") or {}).get("version"), "packages": (e.get("conda") or {}).get("spec"),
                 "outdated": self.outdated(e)}
                for e in self.engines.values() if include_builtin or e.get("optional")]

    def outdated(self, engine):
        """An installed conda tool whose lock file is not the one this Cladia ships (an app update changed it)."""
        if not engine.get("conda") or not self.available(engine):
            return False
        from .conda import lock_hash
        try:
            return installed_locks().get(engine["id"]) != lock_hash(engine["id"], engine["conda"]["version"])
        except (OSError, ValueError):
            return True

    def public(self):
        def engine_data(engine):
            data = {k: v for k, v in engine.items() if k not in {"run", "install", "uninstall"}}
            if engine.get("optional"):
                data["installedChoices"] = self.installed.get(engine["id"], [])
                data["options"] = [{**option,
                    "choices": [c for c in option["choices"] if c["value"] in data["installedChoices"]],
                    "default": option["default"] if option["default"] in data["installedChoices"]
                        else data["installedChoices"][0]}
                    if option.get("choicesFromInstall") and data["installedChoices"] else option
                    for option in engine["options"]]
            return data
        return {
            "categories": sorted((c for c in self.categories.values() if self.operationsFor(c["id"])), key=lambda c: c["order"]),
            "operations": sorted(({k: v for k, v in o.items() if k != "prepare"} for o in self.operations.values() if self.enginesFor(o["id"])),
                                 key=lambda o: (self.categories[o["category"]]["order"], o["order"])),
            "engines": [engine_data(e) for e in
                        sorted((e for e in self.engines.values() if self.available(e)),
                               key=lambda e: (e.get("order", 0), e["label"]))],
        }


def installed_engines():
    try:
        data = json.loads((config.APP_DIR / "installed-engines.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_installed_engine(engine_id, choices, lock=None):
    """An optional tool installed (its install choices; lock: its conda lock file's hash)."""
    installed = installed_engines()
    installed[engine_id] = list(dict.fromkeys(choices))
    _write_json(config.APP_DIR / "installed-engines.json", installed)
    if lock:
        _write_json(config.APP_DIR / "installed-locks.json", {**installed_locks(), engine_id: lock})


def forget_installed_engine(engine_id):
    """An optional tool uninstalled."""
    _write_json(config.APP_DIR / "installed-engines.json", {k: v for k, v in installed_engines().items() if k != engine_id})
    _write_json(config.APP_DIR / "installed-locks.json", {k: v for k, v in installed_locks().items() if k != engine_id})


def installed_locks():
    try:
        data = json.loads((config.APP_DIR / "installed-locks.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def tool_settings(engine_id):
    try:
        data = json.loads((config.APP_DIR / "tool-settings.json").read_text(encoding="utf-8"))
        value = data.get(engine_id, {}) if isinstance(data, dict) else {}
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def tool_setting(engine_id, key, default=None):
    """Read a plugin's local setting, including secrets, only on the server."""
    return tool_settings(engine_id).get(key, default)


def save_tool_settings(engine_id, changes):
    path = config.APP_DIR / "tool-settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict): saved = {}
    except (OSError, ValueError):
        saved = {}
    current = saved.get(engine_id, {})
    if not isinstance(current, dict): current = {}
    for key, value in changes.items():
        if value is None: current.pop(key, None)
        else: current[key] = value
    saved[engine_id] = current
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    if os.name != "nt": tmp.chmod(0o600)
    tmp.replace(path)


def build_registry(plugin_dir=PLUGIN_DIR, installed=None):
    if Path(plugin_dir) == PLUGIN_DIR and installed is None:
        # The settings file also holds local BLAST databases, whose choices are
        # gathered when that plugin is loaded. An installation changes its own file.
        def stamp(path):
            try: return path.stat().st_mtime_ns
            except OSError: return 0
        plugins = tuple((path.name, stamp(path)) for path in sorted(PLUGIN_DIR.glob("*.py")))
        return _cached_registry(str(config.APP_DIR), stamp(config.APP_DIR / "installed-engines.json"),
                                stamp(config.APP_DIR / "settings.json"),
                                stamp(config.APP_DIR / "installed-locks.json"), plugins)
    return _build_registry(plugin_dir, installed)


@lru_cache(maxsize=8)
def _cached_registry(app_dir, installed_stamp, settings_stamp, locks_stamp, plugins):
    return _build_registry(PLUGIN_DIR, None)


def invalidate_registry():
    _cached_registry.cache_clear()


def _build_registry(plugin_dir=PLUGIN_DIR, installed=None):
    from .tool_jobs import HOOKS
    registry = Registry(installed)
    registry.registerCategory({"id": "align", "label": "Align", "icon": "align", "order": 20})
    registry.registerCategory({"id": "annotate", "label": "Annotate", "icon": "annotate", "order": 10})
    registry.registerCategory({"id": "tree", "label": "Build a tree", "toolbarLabel": "Build tree", "icon": "tree", "order": 30})
    registry.registerCategory({"id": "map", "label": "Map reads to a reference", "toolbarLabel": "Map reads", "icon": "map", "order": 40})
    registry.registerCategory({"id": "assemble", "label": "Assemble", "icon": "assemble", "order": 50})
    registry.registerCategory({"id": "variants", "label": "Variants", "icon": "variants", "order": 55})
    registry.registerCategory({"id": "primers", "label": "Primers", "icon": "primers", "order": 60})
    registry.registerCategory({"id": "crispr", "label": "CRISPR", "icon": "crispr", "order": 65})
    registry.registerCategory({"id": "blast", "label": "BLAST", "icon": "blast", "order": 70})
    registry.registerCategory({"id": "download", "label": "Download", "icon": "download", "order": 80})
    for entry in (
        {"id": "annotate.denovo", "category": "annotate", "label": "De novo", "description":
         "Find genes, rRNAs, tRNAs and introns in organelle DNA without a reference.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None}],
         "outputMode": "update", "action": "Annotate", "order": 10},
        {"id": "annotate.reference", "category": "annotate", "label": "From a reference", "description":
         "Copy features from an annotated close relative, finding matching regions and fitting coding sequence ends.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None},
                    {"key": "reference", "label": "Reference", "docType": "annotated_sequence", "min": 1, "max": 1}],
         "outputMode": "update", "action": "Annotate", "order": 20},
        {"id": "annotate.orfs", "category": "annotate", "label": "Find ORFs", "description":
         "Find open reading frames from a start codon to the next stop, using each sequence's genetic code or a chosen one.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None}],
         "outputMode": "update", "action": "Find ORFs", "order": 30},
        {"id": "annotate.trna", "category": "annotate", "label": "tRNAs", "description":
         "Find tRNA genes and add them as gene and tRNA features; the sequences' other annotations stay.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None}],
         "outputMode": "update", "action": "Find tRNAs", "order": 40},
        {"id": "align.new", "category": "align", "label": "New alignment", "description":
         "Aligns sequences base by base from end to end: genes, or genomes with the same gene order. The result goes under Alignments.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "sequence", "min": 2, "max": None}], "action": "Align", "order": 10},
        {"id": "align.add", "category": "align", "label": "Add to existing", "description":
         "Adds sequences to an alignment, or aligns them to one reference sequence. The original is kept; the result is a new alignment.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "sequence", "min": 1, "max": None},
                    {"key": "add_to", "label": "Add to", "docType": "alignment_or_sequence", "min": 1, "max": 1}], "action": "Add", "order": 20},
        {"id": "align.compare", "category": "align", "label": "Compare genomes", "description":
         "Finds shared blocks in genomes that may be rearranged or inverted. The result is a genome comparison.",
         "inputs": [{"key": "sequences", "label": "Genomes", "docType": "dna_sequence", "min": 2, "max": None}], "action": "Compare", "order": 30},
        {"id": "align.trim", "category": "align", "label": "Trim columns", "description":
         "Removes gappy or poorly aligned columns, as before building a tree. The original is kept; the result is a new alignment.",
         "inputs": [{"key": "alignment", "label": "Alignment", "docType": "alignment", "min": 1, "max": 1, "minRows": 2}],
         "defaultName": "alignment_trimmed", "action": "Trim", "order": 40},
        {"id": "tree.build", "category": "tree", "label": "Build tree", "description":
         "Builds a phylogenetic tree from an alignment. The result is a new tree in the library.",
         "inputs": [{"key": "alignment", "label": "Alignment", "docType": "alignment", "min": 1, "max": 1,
                     "minRows": 3}],
         "defaultName": "alignment_tree", "action": "Build tree", "order": 10},
        {"id": "variants.alignment_snps", "category": "variants", "label": "Variant detection", "description":
         "Find SNPs and small indels in a nucleotide alignment relative to one reference row. The VCF is attached to the alignment.",
         "inputs": [{"key": "alignment", "label": "Alignment", "docType": "alignment", "min": 1, "max": 1,
                     "minRows": 2}], "outputMode": "update", "action": "Detect SNPs", "order": 10},
        {"id": "variants.microsatellites", "category": "variants", "label": "Microsatellites", "description":
         "Find perfect, compound, imperfect, or generic tandem repeats in DNA sequences and add repeat_region features. Export annotated sequences as GFF3 if needed.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None}],
         "outputMode": "update", "action": "Find repeats", "order": 20},
        {"id": "map.reads", "category": "map", "label": "Map reads", "description":
         "Short or long reads are mapped to a reference: reads items from the library, or FASTQ / FASTA files on disk (gzipped or not; they stay where they are). The result is a new mapping in the library.",
         "inputs": [{"key": "reference", "label": "Reference", "docType": "dna_sequence", "min": 1, "max": 1},
                    {"key": "read_files", "label": "Reads",
                     "docType": "file_paths", "min": 1, "max": None, "preview": "read_pairs"}],
         "defaultName": "reference_mapping", "fallbackName": "mapping", "destination": "current", "action": "Map reads", "order": 10},
        {"id": "assemble.one", "category": "assemble", "label": "One sample", "description":
         "A sample's Sanger reads are aligned to the longest read and used to call a consensus with disagreements marked as features.",
         "inputs": [{"key": "reads", "label": "Reads", "docType": "sanger_read", "min": 2, "max": None}],
         "defaultName": "sanger_one", "outputLabel": "Name of the consensus", "fallbackName": "consensus",
         "group": "Sanger reads", "action": "Assemble", "order": 10},
        {"id": "assemble.batch", "category": "assemble", "label": "Multiple samples", "description":
         "Reads are grouped by sample name. Each sample yields an alignment and the consensus sequences form a list in a new folder.",
         "inputs": [{"key": "reads", "label": "Reads", "docType": "sanger_read", "min": 2, "max": None,
                     "preview": "sanger_samples"}],
         "defaultName": "sanger_batch", "outputLabel": "Name of the folder for the results", "fallbackName": "Sanger samples",
         "group": "Sanger reads", "action": "Assemble", "order": 20},
        {"id": "assemble.organelle", "category": "assemble", "label": "Organelle from reads", "description":
         "Assemble an organelle genome from Illumina reads. This can take hours and requires seed and label databases.",
         "inputs": [{"key": "read_files", "label": "Illumina reads", "docType": "file_paths", "min": 1, "max": None,
                     "preview": "read_pairs", "extensions": [".fastq", ".fq", ".fastq.gz", ".fq.gz"],
                     "maxGroups": 1}],
         "defaultName": "organelle", "fallbackName": "organelle", "group": "Organelles", "action": "Assemble", "order": 30},
        {"id": "primers.design", "category": "primers", "label": "Design", "description":
         "Find PCR primer pairs flanking a target region. Results show their positions, Tm, GC and product size.",
         "inputs": [{"key": "template", "label": "Template", "docType": "dna_sequence", "min": 1, "max": 1}],
         "prefillTargetFromViewer": True, "outputMode": "auto", "action": "Design", "order": 10},
        {"id": "primers.test", "category": "primers", "label": "Test", "description":
         "Test a primer pair against selected sequences for binding sites, mismatches, product size and orientation.",
         "inputs": [{"key": "sequences", "label": "Sequences", "docType": "dna_sequence", "min": 1, "max": None},
                    {"key": "forward", "label": "Forward primer (5′→3′)", "docType": "text", "min": 1, "max": 1},
                    {"key": "reverse", "label": "Reverse primer (5′→3′)", "docType": "text", "min": 1, "max": 1}],
         "presets": "primer_pairs", "outputMode": "auto", "action": "Test", "order": 20},
        {"id": "blast.search", "category": "blast", "label": "Search", "description":
         "Search sequence databases with selected library sequences, a viewer selection, or graph nodes. Results open as tables.",
         "inputs": [{"key": "queries", "label": "Query", "docType": "blast_queries", "min": 1, "max": None}],
         "outputMode": "none", "action": "BLAST", "engineTabs": True, "engineTabsLabel": "Search location", "order": 10},
        {"id": "download.accessions", "category": "download", "label": "Accessions", "description":
         "Download nucleotide records by accession, including their sequences and annotations.",
         "inputs": [{"key": "accessions", "label": "Accessions", "docType": "accessions", "min": 1, "max": None}],
         "defaultName": "GenBank download", "action": "Download", "order": 10},
        {"id": "download.search", "category": "download", "label": "Search", "description":
         "Search GenBank nucleotide records and tick the ones to download.",
         "inputs": [{"key": "accessions", "label": "Records", "docType": "ncbi_search", "min": 1, "max": None}],
         "defaultName": "GenBank download", "action": "Download", "order": 20},
    ):
        registry.registerOperation({**entry, **({"prepare": HOOKS[entry["id"]]} if entry["id"] in HOOKS else {})})
    for path in sorted(Path(plugin_dir).glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"cladia.tool_plugins.{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            registry.registerEngine(module.ENGINE, module.run, getattr(module, "install", None), getattr(module, "uninstall", None))
        except Exception as exc:
            log.error("Skipping tool engine %s: %s", path.name, exc)
    return registry
