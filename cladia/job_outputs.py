"""Transactional annotation and library result publication services."""
import re
import hashlib
from pathlib import Path
from . import library
from .genbank import read_records, write_record

def named_record(item, used):
    """The item's sequence, under the item's name, unique among `used`."""
    record = library.records(item)[0]
    name = library.safe_name(item["name"])
    while name in used:  # names must be unique in an alignment
        name += "_"
    used.add(name)
    record.id, record.description = name, ""
    return record

def annotated(job, workdir, results, suffix, into, apply=False, busy=None):
    """An annotation job's results [(item, GenBank file)]: new sequences
    `<name><suffix>`, or, for a whole list annotated, one new list named `into`,
    in the list's order, its records named as the sequences they came from."""
    if not results:
        raise RuntimeError("No sequence was annotated (see the log)")
    if apply:
        groups = {}
        for item, path in results:
            groups.setdefault(item["path"], []).append((item, path))
        for group in groups.values():
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            if library.apply_annotation_group(group, busy):
                owner = group[0][0].get("parent") or group[0][0]["id"]
                updated = library.get_item(owner)
                job["outputs"].append({"id": owner, "label": updated["label"], "kind": updated["kind"], "updated": True})
                job["log"] += f"Updated {updated['label']}; its previous state is in saved versions.\n"
            else:
                job["log"] += ("Saved as a copy instead of updating the sequence: it changed or is being edited, or the "
                               "result's sequence differs (e.g. the annotation program rotated a circular genome).\n")
                job["warning"] = "Some annotations could not be safely applied and were saved as copies; see the job log."
                for item, path in group:
                    add_output(job, path, f"{library.safe_name(item['name'])}{suffix}", source=item)
        return
    if not into:
        for item, path in results:
            add_output(job, path, f"{library.safe_name(item['name'])}{suffix}", source=item)
        return
    records = []
    for item, path in results:
        record = read_records(Path(path), "genbank")[0]
        old = record.name
        record.name = re.sub(r"\s+", "_", item["name"].strip()) or record.name
        if record.id in (old, "<unknown id>"):
            record.id = record.name
        records.append(record)
    write_record(records, workdir / "list.gb")
    add_output(job, workdir / "list.gb", into, kind="list", extra={"name": into})

def add_output(job, path, name, kind=None, extra=None, source=None):
    """Copies a result file into the library and links it from the job."""
    with library._writing:
        folder = library.folder_of(source) if source is not None else None
        if source is not None:
            extra = {**(extra or {}), "folder": folder}
        new_id = library.add_output(path, name, kind, extra)
        item = library.get_item(new_id)
        output = {"id": item["id"], "label": item["label"], "kind": item["kind"]}
        if source is not None:
            output.update(folder_source=source.get("parent") or source["id"], folder=folder)
        job["outputs"].append(output)


def create_result_folder(job, name):
    with library._writing:
        job['folder'] = library.add_folder(library.free_folder_name(name, job.get('folder')), job.get('folder'))
    return job['folder']


def attach_alignment_variants(job, item, vcf_text, reference, expected_source_hash, save_copy=False):
    """Publish an alignment VCF as an updated item or an explicit copy."""
    with library._writing:
        if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != expected_source_hash:
            raise ValueError("The alignment changed while variants were being detected; run the tool again")
        target = library.get_item(library.duplicate(item)) if save_copy else library.get_item(item["id"])
        library.save_alignment_variants(target, vcf_text, reference, expected_source_hash)
        current = library.get_item(target["id"])
        job["outputs"].append({"id": current["id"], "label": current["label"], "kind": "alignment",
                               "updated": not save_copy})
        job["log"] += f"Saved variants on {current['label']}.\n"
