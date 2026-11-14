"""Generated-tool input and option validation, independent of HTTP."""
from pathlib import Path
from . import library
from .item_inputs import InputError, _item_in, _sequences
from .genbank import read_records


def validate_tool_request(req):
    from .tool_registry import build_registry
    registry = build_registry()
    operation = registry.operations.get(req.operation)
    engine = registry.engines.get(req.engine)
    if not operation or not engine or not registry.available(engine) or req.operation not in engine["supports"]:
        raise InputError(400, "This tool method is unavailable")
    expected = {slot["key"]: slot for slot in operation["inputs"]}
    if set(req.inputs) != set(expected):
        raise InputError(400, "The tool inputs do not match the selected operation")
    library_types = {"sequence", "dna_sequence", "annotated_sequence", "alignment",
                     "alignment_or_sequence", "sanger_read", "blast_queries"}
    by_id = library.item_map() if any(slot["docType"] in library_types for slot in expected.values()) else {}
    alignment_rows = {}
    resolved = {}
    for key, slot in expected.items():
        ids = req.inputs[key]
        if key == "sequences":
            values = _sequences(ids, by_id)
            if len(values) < slot["min"] or slot.get("max") and len(values) > slot["max"]:
                raise InputError(400, f"Choose at least {slot['min']} sequences")
            proteins = [item["label"] for item in values if library.item_is_protein(item)] if slot["docType"] == "dna_sequence" else []
            if proteins:
                raise InputError(400, f"{slot['label']} must be DNA; these are proteins: {', '.join(proteins)}")
            resolved[key] = values
        elif key == "add_to":
            if len(ids) != 1:
                raise InputError(400, "Choose one alignment or reference sequence")
            item = _item_in(by_id, ids[0])
            if item["kind"] not in {"alignment", "sequence"}:
                raise InputError(400, "Pick an alignment or reference sequence")
            resolved[key] = item
        elif slot["docType"] == "alignment":
            if len(ids) != 1:
                raise InputError(400, "Choose one alignment")
            item = _item_in(by_id, ids[0])
            if item["kind"] != "alignment":
                raise InputError(400, "Choose an alignment")
            alignment_rows[key] = read_records(Path(item["path"]), "fasta")
            minimum, maximum = slot.get("minRows", 1), slot.get("maxRows")
            if len(alignment_rows[key]) < minimum:
                raise InputError(400, f"{operation['label']} needs at least {minimum} alignment rows")
            if maximum is not None and len(alignment_rows[key]) > maximum:
                raise InputError(400, f"{operation['label']} accepts at most {maximum} alignment rows")
            resolved[key] = [item]
        elif slot["docType"] == "dna_sequence":
            if len(ids) != 1:
                raise InputError(400, "Choose one reference sequence")
            item = _item_in(by_id, ids[0])
            if item["kind"] != "sequence" or library.item_is_protein(item):
                raise InputError(400, "Choose a DNA sequence as the reference; proteins cannot be mapped")
            resolved[key] = [item]
        elif slot["docType"] == "annotated_sequence":
            if len(ids) != 1:
                raise InputError(400, "Choose one annotated reference")
            item = _item_in(by_id, ids[0])
            if item["kind"] != "sequence" or library.item_is_protein(item) or not item.get("features"):
                raise InputError(400, "Choose an annotated DNA sequence as the reference")
            resolved[key] = [item]
        elif slot["docType"] == "file_paths":
            if any(not isinstance(path, str) for path in ids):
                raise InputError(400, "Invalid read file path")
            paths = [path.strip().strip('"') for path in ids if path.strip()]
            if len(paths) < slot["min"]:
                raise InputError(400, "Choose the read files")
            if slot.get("extensions") and any(not p.startswith("item:") and  # (item:<id>: a reads item)
                                              not any(p.lower().endswith(ext) for ext in slot["extensions"]) for p in paths):
                raise InputError(400, "Choose FASTQ read files")
            resolved[key] = paths
        elif slot["docType"] == "sanger_read":
            reads = _sequences(ids, by_id)
            if len(reads) < slot["min"]:
                raise InputError(400, "Select at least two reads")
            if any(library.item_is_protein(it) for it in reads):
                raise InputError(400, "Reads are DNA")
            if any(it.get("length", 0) > 3000 for it in reads):
                raise InputError(400, "A selected sequence is longer than a Sanger read (3000 bp)")
            resolved[key] = reads
        elif slot["docType"] == "text":
            value = ids[0].strip() if len(ids) == 1 and isinstance(ids[0], str) else ""
            if len(value) < slot["min"]:
                raise InputError(400, f"Enter {slot['label']}")
            resolved[key] = value
        elif slot["docType"] == "blast_queries":
            if len(ids) < slot["min"]:
                raise InputError(400, "Choose a BLAST query")
            for query in ids:
                item_id = query if isinstance(query, str) else query.get("item") if isinstance(query, dict) else None
                if not isinstance(item_id, str):
                    raise InputError(400, "Invalid BLAST query")
                item = _item_in(by_id, item_id)
                if isinstance(query, str) and item["kind"] != "sequence":
                    raise InputError(400, "Choose sequences for BLAST")
            resolved[key] = ids
        elif slot["docType"] in {"accessions", "ncbi_search"}:
            if not ids or any(not isinstance(a, str) for a in ids):
                raise InputError(400, "Choose accession numbers to download")
            resolved[key] = ids
        else:
            raise InputError(400, f"Unsupported input slot: {key}")
    items = ([it for it in resolved["sequences"] if it["id"] != resolved.get("add_to", {}).get("id")]
             if "sequences" in resolved else resolved.get("alignment") or resolved.get("reference") or resolved.get("reads") or [])
    if "sequences" in expected and len(items) < expected["sequences"]["min"]:
        raise InputError(400, f"Choose at least {expected['sequences']['min']} sequences")
    specs = {option["key"]: option for option in engine["options"]
             if not option.get("operations") or req.operation in option["operations"]}
    if set(req.options) - set(specs):
        raise InputError(400, "Unknown tool option")
    options = {}
    for key, spec in specs.items():
        value = req.options.get(key, spec["default"])
        kind = spec["type"]
        if kind == "choice":
            choices = [c["value"] for c in spec["choices"]]
            if spec.get("choicesFromInstall"):
                choices = [c for c in choices if c in registry.installed.get(engine["id"], [])]
                if key not in req.options and choices and value not in choices:
                    value = choices[0]
            if spec.get("choicesFrom", {}).get("kind") == "alignment_rows":
                slot_key = spec["choicesFrom"]["slot"]
                if slot_key not in alignment_rows:
                    alignment_rows[slot_key] = read_records(Path(resolved[slot_key][0]["path"]), "fasta")
                choices += [r.id for r in alignment_rows[slot_key]]
            if value not in choices:
                raise InputError(400, f"Invalid {spec['label']}")
        if kind == "bool" and not isinstance(value, bool):
            raise InputError(400, f"Invalid {spec['label']}")
        if kind in ("int", "float") and value is not None:
            if isinstance(value, bool) or not isinstance(value, int if kind == "int" else (int, float)):
                raise InputError(400, f"Invalid {spec['label']}")
            if spec.get("min") is not None and value < spec["min"] or spec.get("max") is not None and value > spec["max"]:
                raise InputError(400, f"Invalid {spec['label']}")
        if kind in ("text", "file") and not isinstance(value, str):
            raise InputError(400, f"Invalid {spec['label']}")
        options[key] = value
    for key, spec in specs.items():
        if spec["type"] == "choice":
            choice = next((c for c in spec["choices"] if c["value"] == options[key]), None)
            condition = choice.get("when") if choice else None
            if condition and options.get(condition["key"]) not in condition["values"]:
                raise InputError(400, f"{spec['label']} does not match the selected {condition['key']}")
    if items and library.item_is_protein(items[0]):
        for key, spec in specs.items():
            if spec.get("dnaOnly") and spec["type"] == "bool":
                options[key] = False
    return operation, engine, resolved, items, options
