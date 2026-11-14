"""Primer3 primer design engine."""
import time
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "packages": [
            "primer3"
        ],
        "estimateBytes": 1000000
    },
    "id": "primer3", "label": "Primer3", "blurb": "Design primer pairs around a target region.",
    "supports": ["primers.design"], "order": 10, "lane": "compute", "cores": 1, "jobKind": "primers",
    "options": [
        {"key": "target_start", "label": "Target from", "type": "int", "default": None, "min": 1},
        {"key": "target_end", "label": "to", "type": "int", "default": None, "min": 1},
        {"key": "product_min", "label": "Product min", "type": "int", "default": 200, "min": 20, "max": 20000},
        {"key": "product_max", "label": "Product max", "type": "int", "default": 1000, "min": 20, "max": 20000},
        {"key": "tm_min", "label": "Tm min", "type": "float", "default": 57.0, "min": 30, "max": 90, "advanced": True},
        {"key": "tm_opt", "label": "Tm opt", "type": "float", "default": 60.0, "min": 30, "max": 90, "advanced": True},
        {"key": "tm_max", "label": "Tm max", "type": "float", "default": 63.0, "min": 30, "max": 90, "advanced": True},
        {"key": "gc_min", "label": "GC min", "type": "float", "default": 30.0, "min": 0, "max": 100, "advanced": True},
        {"key": "gc_max", "label": "GC max", "type": "float", "default": 70.0, "min": 0, "max": 100, "advanced": True},
        {"key": "num_return", "label": "Pairs", "type": "int", "default": 5, "min": 1, "max": 20, "advanced": True},
    ],
}


def run(job, workdir, operation, items, output, add_to=None, **options):
    return run_design(job, workdir, item=items[0], **options)


def run_design(job, workdir, item, target_start=None, target_end=None, product_min=200,
                      product_max=1000, tm_min=57.0, tm_opt=60.0, tm_max=63.0,
                      gc_min=30.0, gc_max=70.0, num_return=5):
    """Primer3 on one sequence; target_start/end are 0-based inclusive.

    Circular sequences: a target is rotated to the middle of one copy; without
    a target the sequence is doubled so a product may cross the origin."""
    api.steps(job, "Preparing", "Designing primers", "Reading results")
    record = api.records(item)[0]
    sequence = str(record.seq).upper()
    n = len(sequence)
    circular = record.annotations.get("topology") == "circular"
    rotation = 0
    template = sequence
    target0, target1 = target_start, target_end
    if circular:
        template, rotation, target0, target_len = api.primers_circular_design_template(sequence, target_start, target_end)
        target1 = target0 + target_len - 1 if target0 is not None and target_len else None
        product_max = min(int(product_max), n)
    if product_min > product_max:
        raise RuntimeError("The product-size range is empty (minimum above maximum)")
    api.step(job, 0, None, item["label"])
    text = api.primers_design_input(template, target0, target1, product_min, product_max,
                                tm_min, tm_opt, tm_max, gc_min, gc_max, num_return)
    (workdir / "primer3.in").write_text(text, encoding="utf-8")
    api.ensure_tools_image(job)
    api.step(job, 1, None, "Primer3")
    out = api.run_container(job, api.tools_image(), ["primer3_core", "/data/primer3.in"], workdir)
    api.step(job, 2, None, "reading Primer3's output")
    pairs, warnings = api.primers_parse_design(out)
    pairs = api.primers_map_design(pairs, rotation, n, circular)
    job["log"] += (f"{item['label']}: {len(pairs)} primer pair{'s' if len(pairs) != 1 else ''}"
                   f"{' (circular, target rotated)' if circular and target0 is not None else ''}\n")
    if not pairs:
        job["log"] += "No pair satisfied the constraints (see Primer3's explanation in the output)\n"
    result = {
        "mode": "design",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "template": {"id": item["id"], "label": item["label"], "length": n, "circular": circular},
        "target": None if target_start is None else {"start": target_start + 1, "end": target_end + 1},
        "parameters": {"product_min": product_min, "product_max": product_max, "tm_min": tm_min,
                       "tm_opt": tm_opt, "tm_max": tm_max, "gc_min": gc_min, "gc_max": gc_max,
                       "num_return": num_return},
        "pairs": pairs,
        "warnings": warnings,
    }
    new_id = api.add_primers(result, f"{item['label']} primers")
    job["outputs"].append({"id": new_id, "label": api.get_item(new_id)["label"], "kind": "primers"})
