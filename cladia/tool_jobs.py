"""Core preparation for generated tool jobs, independent of HTTP handlers."""
from . import blast_support
import re
from pathlib import Path
from Bio.Data import CodonTable
from .item_inputs import InputError, _item_in

from . import blastdb, graph, job_outputs, jobs, library, mapping, primers, reads, sanger
from .genbank import looks_protein


def _annotation_batches(ids, suffix, leave_out=None):
    every = library.items()
    by_id = {item["id"]: item for item in every}
    found, loose, seen = [], [], {leave_out}
    for item in sorted((by_id[item_id] for item_id in ids), key=lambda item: item["kind"] != "list"):
        members = [child for child in library.children(item, every)
                   if child["kind"] == "sequence" and child["id"] not in seen]
        seen.update(member["id"] for member in members)
        if item["kind"] == "list" and members:
            found.append((members, f"{item['name']}{suffix}", f"{item['label']} ({len(members)} sequences)"))
        else:
            loose.extend(members)
    if loose:
        found.append((loose, None, loose[0]["label"] if len(loose) == 1 else f"{len(loose)} sequences"))
    return found


def _annotation_options(items, save_copy, busy):
    if save_copy:
        return {"apply": False}
    library.annotation_baselines(items)
    return {"apply": True, "busy": busy}


def submit_denovo(req, resolved, items, engine, options, busy):
    """One job per list (results kept as a list) and one for the loose sequences."""
    return [jobs.submit(engine.get("jobKind", engine["id"]), f"{engine['label']}: {label}", engine["run"],
                        folder=library.folder_of(members[0]), resources=(engine["lane"], engine["cores"]),
                        operation=req.operation, items=members, output=req.output, into=into,
                        **options, **_annotation_options(members, req.save_copy, busy))
            for members, into, label in _annotation_batches(req.inputs["sequences"], f"_{engine['id']}")]


def submit_reference(req, resolved, items, engine, options, busy):
    reference = resolved["reference"][0]
    if not [item for item in items if item["id"] != reference["id"]]:
        raise InputError(400, "Select the sequences to annotate (other than the reference)")
    limit = min(max(options["limit"], 0), 100) / 100
    return [jobs.submit("reference", f"Annotate {label} from {reference['label']}", engine["run"],
                        folder=library.folder_of(members[0]), cores=min(len(members), jobs.budget()),
                        resources=(engine["lane"], engine["cores"]), operation=req.operation, items=members,
                        output=req.output, reference=reference, limit=limit, replace=options["replace"],
                        into=into, **_annotation_options(members, req.save_copy, busy))
            for members, into, label in _annotation_batches(req.inputs["sequences"], "_annotated",
                                                              leave_out=reference["id"])]


def submit_orfs(req, resolved, items, engine, options, busy):
    if options["code"] and options["code"] not in CodonTable.unambiguous_dna_by_id:
        raise InputError(400, f"Unknown genetic code: {options['code']}")
    return [jobs.submit("orfs", f"Find ORFs: {label}", engine["run"], folder=library.folder_of(members[0]),
                        cores=1, resources=(engine["lane"], engine["cores"]), operation=req.operation,
                        items=members, output=req.output, into=into, **options,
                        **_annotation_options(members, req.save_copy, busy))
            for members, into, label in _annotation_batches(req.inputs["sequences"], "_orfs")]


def submit_trnas(req, resolved, items, engine, options, busy):
    return [jobs.submit(engine.get("jobKind", engine["id"]), f"Find tRNAs: {label}", engine["run"],
                        folder=library.folder_of(members[0]), resources=(engine["lane"], engine["cores"]),
                        operation=req.operation, items=members, output=req.output, into=into, **options,
                        **_annotation_options(members, req.save_copy, busy))
            for members, into, label in _annotation_batches(req.inputs["sequences"], "_trnas")]


def _folder(req, items):
    return library.result_folder(items, req.folder if "folder" in req.model_fields_set else library.DEFAULT_FOLDER)


def submit_download(req, resolved, items, engine, options, busy):
    accessions = list(dict.fromkeys(a for raw in resolved["accessions"] for a in re.split(r"[\s,;]+", raw) if a))
    if not accessions:
        raise InputError(400, "Type or paste one or more accessions (e.g. NC_001677, MK000001.1)")
    bad = [a for a in accessions if not re.fullmatch(r"[A-Za-z]{1,6}_?\d+(\.\d+)?|\d+", a)]
    if bad:
        raise InputError(400, f"Not GenBank accessions: {', '.join(bad[:10])}")
    first = accessions[0] + (f" and {len(accessions) - 1} more" if len(accessions) > 1 else "")
    return jobs.submit("download", f"Download {first}", engine["run"], folder=_folder(req, []),
                       resources=(engine["lane"], engine["cores"]), operation=req.operation, items=[],
                       output=req.output.strip() or "GenBank download", accessions=accessions,
                       as_list=options["as_list"])


def submit_primers(req, resolved, items, engine, options, busy):
    low = max(20, min(int(options["product_min"]), 20000))
    high = max(20, min(int(options["product_max"]), 20000))
    if low > high:
        raise InputError(400, "The product-size range is empty (minimum above maximum)")
    options = {**options, "product_min": low, "product_max": high}
    if req.operation == "primers.design":
        template = resolved["template"][0]
        record = library.records(template)[0]
        start, end = options.pop("target_start"), options.pop("target_end")
        if (start is None) != (end is None):
            raise InputError(400, "Give both the target's start and end, or neither")
        if start is not None:
            n = len(record)
            if not 1 <= start <= n or not 1 <= end <= n:
                raise InputError(400, f"The target must be within the sequence (1 to {n:,})")
            if start > end and record.annotations.get("topology") != "circular":
                raise InputError(400, "The target's start is after its end")
            start, end = start - 1, end - 1
        options["target_start"], options["target_end"] = start, end
        for key in ("tm_min", "tm_opt", "tm_max"):
            options[key] = max(30.0, min(float(options[key]), 90.0))
        if not options["tm_min"] <= options["tm_opt"] <= options["tm_max"]:
            raise InputError(400, "The Tm range must have minimum ≤ optimum ≤ maximum")
        for key in ("gc_min", "gc_max"):
            options[key] = max(0.0, min(float(options[key]), 100.0))
        if options["gc_min"] > options["gc_max"]:
            raise InputError(400, "The GC range must have minimum ≤ maximum")
        options["num_return"] = max(1, min(int(options["num_return"]), 20))
        return [jobs.submit("primers", f"Design primers: {template['label']}", engine["run"],
                            folder=_folder(req, [template]), resources=(engine["lane"], engine["cores"]),
                            operation=req.operation, items=[template], output=req.output, **options)]
    try:
        forward = primers.validate(resolved["forward"], "forward primer")
        reverse = primers.validate(resolved["reverse"], "reverse primer")
    except ValueError as exc:
        raise InputError(400, str(exc)) from exc
    label = items[0]["label"] if len(items) == 1 else f"{len(items)} sequences"
    return [jobs.submit("pcr", f"Test primers: {label}", engine["run"], folder=_folder(req, items),
                        resources=(engine["lane"], engine["cores"]), operation=req.operation,
                        items=items, output=req.output, forward=forward, reverse=reverse, **options)]


def _run_blast_list(job, workdir, runner, operation, items, output, queries, list_name, search_options):
    failures = 0
    try:
        for index, (item, query) in enumerate(zip(items, queries, strict=True), start=1):
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            query_dir = workdir / str(index)
            query_dir.mkdir()
            job["log"] += f"\nQuery {index}/{len(queries)}: {query['name']}\n"
            try:
                runner(job, query_dir, operation=operation, items=[item], output=output,
                       query=query, **search_options)
            except Exception as exc:
                if job.get("cancel"):
                    raise
                failures += 1
                job["log"] += f"{query['name']} failed: {exc}\n"
    finally:
        job_outputs.group_results(job, list_name)
    if not job["outputs"]:
        raise RuntimeError("No query finished successfully (see the log)")
    if failures:
        job["warning"] = f"{failures} of {len(queries)} BLAST queries failed (see the log)."


def submit_blast(req, resolved, items, engine, options, busy):
    program, database = options["program"], options["database"]
    if program not in blast_support.BLAST_PROGRAMS:
        raise InputError(400, f"Unknown program: {program}")
    local = database.startswith("local:")
    if local != (engine["id"] == "local_blast"):
        raise InputError(400, "Choose a database for the selected BLAST engine")
    db = None
    if local:
        db = next((entry for entry in blastdb.databases() if entry["name"] == database[6:]), None)
        if not db or db["missing"]:
            raise InputError(400, f"No BLAST database {database[6:]} on this PC (Settings > BLAST databases)")
        if db["type"] != blast_support.LOCAL_DB_TYPE[program]:
            raise InputError(400, f"{db['name']} cannot be searched with {program}")
    queries = resolved["queries"]
    if not local and len(queries) > 10:
        raise InputError(400, "At most 10 queries at a time at NCBI")
    by_id = library.item_map()
    graphs = {}
    groups, prepared = {}, []
    query_lists = resolved.get("query_lists", {})
    search_options = {"program": program, "max_hits": min(max(options["max_hits"], 1), 500),
                      "code": req.context.get("code", 1)}
    if local:
        search_options["db"] = db
    else:
        search_options.update(database=database, entrez=options.get("entrez", "").strip())
    for entry in queries:
        query_spec = {"item": entry} if isinstance(entry, str) else entry
        item = _item_in(by_id, query_spec["item"])
        if query_spec.get("node"):
            try:
                if item["id"] not in graphs:
                    graphs[item["id"]] = library.graph_data(item)
                sequence = graph.oriented(graphs[item["id"]], query_spec["node"], "+")
            except (KeyError, ValueError) as exc:
                raise InputError(400, f"Node {query_spec['node']}: {exc}") from exc
            name = f"{item['name']} node {query_spec['node']}"
        elif query_spec.get("sequence"):
            sequence = re.sub(r"[^A-Za-z*]", "", query_spec["sequence"])
            name = (query_spec.get("name") or item["name"]) + (
                f" {query_spec['feature']}" if query_spec.get("feature") else
                f" {query_spec['start']:,}-{query_spec['end']:,}" if query_spec.get("start") else "")
        else:
            sequence, name = str(library.records(item)[0].seq), item["name"]
        if not sequence:
            raise InputError(400, f"{name}: no sequence")
        protein = looks_protein(sequence)
        if protein != (program in ("blastp", "tblastn")):
            raise InputError(400, f"{name} is {'a protein' if protein else 'DNA'}: "
                                     f"{program} needs {'DNA' if protein else 'a protein'}")
        query = {"name": name, "sequence": sequence, "item": item["id"],
                 "start": query_spec.get("start") or 1, "end": query_spec.get("end") or len(sequence)}
        list_id = query_lists.get(entry) if isinstance(entry, str) else None
        if list_id:
            if list_id not in groups:
                groups[list_id] = []
                prepared.append((by_id[list_id], groups[list_id]))
            groups[list_id].append((item, query))
        else:
            prepared.append((item, [(item, query)]))

    submitted = []
    kind = "localblast" if local else "blast"
    database_label = db["name"] if local else database
    for source, batch in prepared:
        members, specs = map(list, zip(*batch))
        if source["kind"] == "list":
            runner = _run_blast_list
            label = f"{source['label']} ({len(specs)} queries)"
            params = {"runner": engine["run"], "queries": specs,
                      "list_name": f"{source['name']}_blast", "search_options": search_options}
        else:
            runner = engine["run"]
            label = specs[0]["name"]
            params = {"query": specs[0], **search_options}
        submitted.append(jobs.submit(kind, f"BLAST {label} ({program}, {database_label})",
                                     runner, folder=library.folder_of(source), operation=req.operation,
                                     resources=(engine["lane"], engine["cores"]),
                                     items=members, output=req.output, **params))
    return submitted


def _read_groups(paths, read_type="illumina"):
    """Read files on disk, or reads items ("item:<id>", reads.read_groups), as samples."""
    if read_type not in mapping.READ_TYPES:
        raise InputError(400, "Unknown read type")
    if not paths:
        raise InputError(400, "Pick the reads (from the library or files on disk)")
    try:
        return reads.read_groups(paths, read_type)
    except ValueError as e:
        raise InputError(400, str(e))


def submit_comparison(req, resolved, items, engine, options, busy):
    return jobs.submit(engine.get("jobKind", engine["id"]), f"Compare {len(items)} genomes", engine["run"],
                       folder=_folder(req, items), resources=(engine["lane"], engine["cores"]),
                       operation=req.operation, items=items, add_to=None,
                       output=library.safe_name(req.output) or "alignment", **options)


def submit_map(req, resolved, items, engine, options, busy):
    if options.get("extend") and options["read_type"] not in {"illumina", "hifi"}:
        raise InputError(400, "Extension rounds are currently supported for Illumina and HiFi reads only")
    groups = _read_groups(resolved["read_files"], options.get("read_type", "illumina"))
    return jobs.submit(engine.get("jobKind", engine["id"]), f"Map reads: {items[0]['label']}", engine["run"],
                       folder=_folder(req, []), resources=(engine["lane"], engine["cores"]),
                       operation=req.operation, items=items, add_to=None,
                       output=library.safe_name(req.output) or "mapping", groups=groups, **options)


def submit_assembly(req, resolved, items, engine, options, busy):
    output = library.safe_name(req.output) or "alignment"
    extra = {}
    if req.operation == "assemble.organelle":
        groups = _read_groups(resolved["read_files"], options.get("read_type", "illumina"))
        if len(groups) != 1:
            raise InputError(400, "Choose one sample's paired or single-end reads")
        extra["groups"] = groups
        title = f"{engine['label']}: {req.operation.rsplit('.', 1)[-1]} from reads"
    elif req.operation == "assemble.batch":
        paired, _ = sanger.samples_by_name([item["name"] for item in items])
        by_name = {}
        for item in items:
            by_name.setdefault(item["name"], []).append(item)
        samples = [(name, [item for read in names for item in by_name.pop(read, [])])
                   for name, names in paired.items()]
        if not samples:
            raise InputError(400, "No two reads share a sample name (e.g. AG123_F and AG123_R)")
        extra["samples"] = samples
        output = req.output.strip().replace("/", "-") or "Sanger samples"
        title = f"Assemble {len(samples)} samples"
    else:
        title = f"Assemble {len(items)} reads"
    return jobs.submit(engine.get("jobKind", engine["id"]), title, engine["run"],
                       folder=_folder(req, items), resources=(engine["lane"], engine["cores"]),
                       operation=req.operation, items=items, add_to=None, output=output, **extra, **options)


def submit_alignment_snps(req, resolved, items, engine, options, busy):
    item = resolved["alignment"][0]
    if not options.get("reference_row"):
        raise InputError(400, "Choose a reference row")
    return jobs.submit(engine.get("jobKind", engine["id"]), f"Detect variants: {item['label']}", engine["run"],
                       folder=library.folder_of(item), resources=(engine["lane"], engine["cores"]),
                       operation=req.operation, items=[item], output="", save_copy=req.save_copy, **options)


def submit_microsatellites(req, resolved, items, engine, options, busy):
    return [jobs.submit(engine.get("jobKind", engine["id"]), f"Find microsatellites: {label}", engine["run"],
                        folder=library.folder_of(members[0]), resources=(engine["lane"], engine["cores"]),
                        operation=req.operation, items=members, output=req.output, into=into, **options,
                        **_annotation_options(members, req.save_copy, busy))
            for members, into, label in _annotation_batches(req.inputs["sequences"], "_microsatellites")]


HOOKS = {
    "annotate.denovo": submit_denovo,
    "annotate.reference": submit_reference,
    "annotate.orfs": submit_orfs,
    "annotate.trna": submit_trnas,
    "primers.design": submit_primers,
    "primers.test": submit_primers,
    "blast.search": submit_blast,
    "download.accessions": submit_download,
    "download.search": submit_download,
    "align.compare": submit_comparison,
    "map.reads": submit_map,
    "assemble.one": submit_assembly,
    "assemble.batch": submit_assembly,
    "assemble.organelle": submit_assembly,
    "variants.alignment_snps": submit_alignment_snps,
    "variants.microsatellites": submit_microsatellites,
}
