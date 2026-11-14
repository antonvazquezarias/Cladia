"""In-silico PCR testing engine."""
import time
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "builtin"
    },
    "id": "primer_test", "label": "In-silico PCR", "blurb": "Check primer binding and possible products on DNA sequences.",
    "supports": ["primers.test"], "order": 10, "lane": "pcr", "cores": 0, "jobKind": "pcr",
    "options": [
        {"key": "product_min", "label": "Product min", "type": "int", "default": 50, "min": 20, "max": 20000},
        {"key": "product_max", "label": "Product max", "type": "int", "default": 2000, "min": 20, "max": 20000},
        {"key": "mismatches", "label": "Mismatches allowed", "type": "int", "default": 0, "min": 0, "max": 5},
        {"key": "three_prime", "label": "Of them, in each primer's last 5 bases (3′ end)",
         "type": "choice", "default": 1, "choices": [
             {"value": 0, "label": "none"}, {"value": 1, "label": "at most 1"},
             {"value": 2, "label": "at most 2"}, {"value": None, "label": "any"}]},
        {"key": "both_orientations", "label": "Also test the reverse-complement orientation", "type": "bool", "default": True},
    ],
}


def run(job, workdir, operation, items, output, add_to=None, forward="", reverse="", **options):
    return run_test(job, workdir, items=items, forward=forward, reverse=reverse, **options)


def run_test(job, workdir, items, forward, reverse, mismatches=0, product_min=50,
                    product_max=2000, both_orientations=True, three_prime=None):
    """In-silico PCR of one primer pair against the sequences."""
    api.steps(job, "Reading sequences", "Finding binding sites", "Collecting products")
    api.step(job, 0, None, f"{len(items)} sequence{'s' if len(items) != 1 else ''}")
    all_hits = []
    for i, item in enumerate(items):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        record = api.records(item)[0]
        hits = api.primers_test_pair(str(record.seq), forward, reverse, mismatches, product_min,
                                 product_max, record.annotations.get("topology") == "circular",
                                 both_orientations, three_prime)
        for hit in hits:
            hit["item"] = item["id"]
            hit["label"] = item["label"]
        all_hits += hits
        job["log"] += f"{item['label']}: {len(hits)} product{'s' if len(hits) != 1 else ''}\n"
        api.step(job, 1, (i + 1) / len(items), f"{i + 1} of {len(items)} sequences")
        if len(all_hits) >= 1000:
            job["log"] += "Stopped at 1000 products\n"
            break
    api.step(job, 2, 1, f"{len(all_hits)} product{'s' if len(all_hits) != 1 else ''}")
    label = items[0]["label"] if len(items) == 1 else f"{len(items)} sequences"
    result = {
        "mode": "test",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "forward": api.primers_normalize(forward),
        "reverse": api.primers_normalize(reverse),
        "parameters": {"mismatches": mismatches, "three_prime": three_prime, "product_min": product_min,
                       "product_max": product_max, "both_orientations": both_orientations},
        "hits": all_hits,
        "sequences": len(items),
    }
    new_id = api.add_primers(result, f"{label} PCR")
    job["outputs"].append({"id": new_id, "label": api.get_item(new_id)["label"], "kind": "primers"})
