"""Krait-family repeat discovery (perfect, compound, imperfect and generic tandem
repeats) with pytrf's command line.

An optional tool: Settings › Tools › Install downloads pytrf 1.5.0 and pyfastx 2.3.1
from bioconda into their own environment in Cladia's Linux (conda.py; lock:
locks/krait-1.5.0.lock). Results become repeat_region features.
"""
from cladia import engine_api as api

PYTRF_VERSION = "1.5.0"
CITATIONS = [{"tool": "pytrf", "reference": "Du et al. (2025), BMC Bioinformatics 26:151",
              "url": "https://doi.org/10.1186/s12859-025-06168-3"}]
METHODS = {"perfect": "Perfect SSRs", "compound": "Compound SSRs",
           "imperfect": "Imperfect SSRs", "generic": "Generic tandem repeats"}


def _option(key, label, kind, default, methods, **extra):
    return {"key": key, "label": label, "type": kind, "default": default, "advanced": True,
            "visibleWhen": {"key": "method", "values": methods}, **extra}


PERFECT = ["perfect", "compound"]
ENGINE = {
    "id": "krait", "label": "Krait repeat finder", "order": 10,
    "blurb": "Find perfect, compound, imperfect, or generic tandem repeats with pytrf.",
    "supports": ["variants.microsatellites"], "optional": True,
    "conda": {"version": PYTRF_VERSION, "spec": "pytrf=1.5.0 pyfastx=2.3.1"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/krait-*"],
                "estimateBytes": 170_000_000},
    "lane": "compute", "cores": 1, "jobKind": "krait",
    "options": [
        {"key": "method", "label": "Repeat search", "type": "choice", "default": "perfect",
         "choices": [{"value": key, "label": value} for key, value in METHODS.items()]},
        _option("mono", "Minimum mononucleotide repeats", "int", 12, PERFECT, min=2),
        _option("di", "Minimum dinucleotide repeats", "int", 7, PERFECT, min=2),
        _option("tri", "Minimum trinucleotide repeats", "int", 5, PERFECT, min=2),
        _option("tetra", "Minimum tetranucleotide repeats", "int", 4, PERFECT, min=2),
        _option("penta", "Minimum pentanucleotide repeats", "int", 4, PERFECT, min=2),
        _option("hexa", "Minimum hexanucleotide repeats", "int", 4, PERFECT, min=2),
        _option("max_distance", "Maximum gap between adjacent SSRs (bp)", "int", 10, ["compound"], min=0),
        _option("atr_min_motif", "Minimum motif length", "int", 1, ["imperfect"], min=1),
        _option("atr_max_motif", "Maximum motif length", "int", 6, ["imperfect"], min=1, max=6),
        _option("seed_repeats", "Minimum seed repeats", "int", 3, ["imperfect"], min=2),
        _option("seed_length", "Minimum seed length (bp)", "int", 10, ["imperfect"], min=2),
        _option("max_errors", "Maximum consecutive errors", "int", 3, ["imperfect"], min=0),
        _option("min_identity", "Minimum identity (%)", "float", 70, ["imperfect"], min=0, max=100),
        _option("max_extend", "Maximum extension length (bp)", "int", 2000, ["imperfect"], min=1),
        _option("gtr_min_motif", "Minimum motif length", "int", 10, ["generic"], min=1),
        _option("gtr_max_motif", "Maximum motif length", "int", 100, ["generic"], min=1, max=1000),
        _option("gtr_min_repeats", "Minimum repeats", "int", 3, ["generic"], min=2),
        _option("gtr_min_length", "Minimum repeat length (bp)", "int", 10, ["generic"], min=2),
        {"key": "replace", "label": "Replace earlier results from this method", "type": "bool",
         "default": True, "advanced": True},
    ],
}


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _check_version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _cli(job, workdir, args):
    """pytrf's command line in its environment (files under /data, the job folder)."""
    return api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["pytrf", *map(str, args)]), workdir).strip()


def _check_version(job, workdir):
    observed = _cli(job, workdir, ["--version"])
    if observed != f"pytrf {PYTRF_VERSION}":
        raise RuntimeError(f"Expected pytrf {PYTRF_VERSION}, got {observed!r}; reinstall it in Settings › Tools")
    return observed

def _validate(method, parameters):
    if method not in METHODS:
        raise ValueError("Choose a repeat search method")
    keys = ("mono", "di", "tri", "tetra", "penta", "hexa", "max_distance",
            "atr_min_motif", "atr_max_motif", "seed_repeats", "seed_length", "max_errors", "max_extend",
            "gtr_min_motif", "gtr_max_motif", "gtr_min_repeats", "gtr_min_length")
    if any(type(parameters[key]) is not int or parameters[key] < (0 if key in {"max_distance", "max_errors"} else 1)
           for key in keys):
        raise ValueError("Repeat settings must be nonnegative integer values")
    if any(parameters[key] < 2 for key in ("mono", "di", "tri", "tetra", "penta", "hexa",
                                                 "seed_repeats", "seed_length", "gtr_min_repeats", "gtr_min_length")):
        raise ValueError("Repeat and length thresholds must be at least two")
    if (parameters["atr_min_motif"] > parameters["atr_max_motif"] or parameters["atr_max_motif"] > 6 or
            parameters["gtr_min_motif"] > parameters["gtr_max_motif"] or parameters["gtr_max_motif"] > 1000):
        raise ValueError("Minimum motif length must not exceed maximum motif length (at most 1000)")
    if type(parameters["min_identity"]) not in {int, float} or not 0 <= parameters["min_identity"] <= 100:
        raise ValueError("Identity must be between 0 and 100 percent")


def _command(method, params, source, destination):
    if method in PERFECT:
        args = ["findstr", "-r", *(params[key] for key in ("mono", "di", "tri", "tetra", "penta", "hexa"))]
    elif method == "imperfect":
        args = ["findatr", "-m", params["atr_min_motif"], "-M", params["atr_max_motif"],
                "-r", params["seed_repeats"], "-l", params["seed_length"],
                "-e", params["max_errors"], "-p", params["min_identity"], "-x", params["max_extend"]]
    else:
        args = ["findgtr", "-m", params["gtr_min_motif"], "-M", params["gtr_max_motif"],
                "-r", params["gtr_min_repeats"], "-l", params["gtr_min_length"]]
    return [*args, "-f", "tsv", "-o", destination, source]


def _rows(path, method, sequence_length):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != (16 if method == "imperfect" else 7) or fields[0] != "target":
            raise ValueError("Unexpected pytrf result row")
        start, end, motif = int(fields[1]), int(fields[2]), fields[3]
        if not (1 <= start <= end <= sequence_length) or not motif or set(motif.upper()) - set("ACGTRYSWKMBDHVN"):
            raise ValueError("Invalid pytrf repeat coordinates or motif")
        repeats = float(fields[5]) if method == "imperfect" else int(fields[5])
        rows.append({"start": start - 1, "end": end - 1, "motif": motif,
                     "repeats": repeats, "length": int(fields[6]),
                     "extra": fields[7:]})
    return rows


def _compound(rows, max_distance):
    groups, group = [], []
    for row in sorted(rows, key=lambda r: (r["start"], r["end"])):
        if group and row["start"] - max(member["end"] for member in group) - 1 > max_distance:
            if len({member["motif"] for member in group}) > 1:
                groups.append(group)
            group = []
        group.append(row)
    if len({member["motif"] for member in group}) > 1:
        groups.append(group)
    return [{"start": group[0]["start"], "end": max(row["end"] for row in group),
             "motif": ",".join(row["motif"] for row in group), "repeats": len(group),
             "length": max(row["end"] for row in group) - group[0]["start"] + 1,
             "extra": [f"{row['motif']}x{row['repeats']}@{row['start'] + 1}-{row['end'] + 1}"
                       for row in group]} for group in groups]


def _features(rows, method):
    found = []
    for row in rows:
        motif, repeats = row["motif"], row["repeats"]
        name = (f"Compound SSR ({repeats} parts)" if method == "compound" else
                f"{METHODS[method]} ({motif}) x {repeats}")
        notes = {"label": [name], "rpt_type": ["tandem"],
                 "inference": [f"program:pytrf:{PYTRF_VERSION}"], "krait_method": [method],
                 "note": [f"{METHODS[method]}: {row['length']} bp; {repeats} repeat(s)"]}
        if method != "compound":
            notes["rpt_unit_seq"] = [motif]
        else:
            notes["note"].append("Components: " + "; ".join(row["extra"]))
        if method == "imperfect":
            keys = ("seed_start", "seed_end", "seed_repeats", "seed_length", "matches",
                    "substitutions", "insertions", "deletions", "identity")
            notes["note"].append("; ".join(f"{key}={value}" for key, value in zip(keys, row["extra"])))
        found.append({"name": name, "type": "repeat_region", "start": row["start"], "end": row["end"],
                      "strand": 1, "notes": notes})
    return found


def _same_method(feature, method):
    notes = feature.get("notes", {})
    if feature.get("type") != "repeat_region":
        return False
    if method in notes.get("krait_method", []):
        return True
    # Version 1.0.0 had no method qualifier and found only perfect SSRs.
    return method == "perfect" and not notes.get("krait_method") and any(
        value.startswith("program:pytrf:") for value in notes.get("inference", []))


def run(job, workdir, operation, items, output, into=None, apply=False, busy=None,
        method="perfect", mono=12, di=7, tri=5, tetra=4, penta=4, hexa=4, max_distance=10,
        atr_min_motif=1, atr_max_motif=6, seed_repeats=3, seed_length=10, max_errors=3,
        min_identity=70, max_extend=2000, gtr_min_motif=10, gtr_max_motif=100,
        gtr_min_repeats=3, gtr_min_length=10, replace=True):
    if operation != "variants.microsatellites" or not items:
        raise ValueError("Choose DNA sequences for repeat detection")
    params = locals().copy()
    _validate(method, params)
    if type(replace) is not bool:
        raise ValueError("Invalid replace setting")
    _check_version(job, workdir)
    api.record_tool(job, "pytrf", PYTRF_VERSION, CITATIONS)
    api.steps(job, f"Searching {METHODS[method]}", "Saving repeat annotations")
    results = []
    for index, item in enumerate(items):
        if job.get("cancel"):
            raise RuntimeError("cancelled")
        record = api.records(item)[0]
        sequence = str(record.seq).upper()
        if item["kind"] != "sequence" or item.get("protein") or not sequence or set(sequence) - set("ACGTRYSWKMBDHVN"):
            raise ValueError(f"{item['label']}: choose a DNA sequence without gaps")
        fasta, table = workdir / f"input{index}.fa", workdir / f"repeats{index}.tsv"  # (/data/... in Linux)
        fasta.write_text(f">target\n{sequence}\n", encoding="ascii")
        api.step(job, 0, index / len(items), f"Searching {item['label']} ({index + 1} of {len(items)})")
        _cli(job, workdir, _command(method, params, f"/data/{fasta.name}", f"/data/{table.name}"))
        rows = _rows(table, method, len(sequence))
        if method == "compound":
            rows = _compound(rows, max_distance)
        features = _features(rows, method)
        data = api.record_to_editor(record)
        if replace:
            data["features"] = [feature for feature in data["features"] if not _same_method(feature, method)]
        existing = {(feature["start"], feature["end"], tuple(feature.get("notes", {}).get("krait_method", [])))
                    for feature in data["features"] if feature["type"] == "repeat_region"}
        fresh = [feature for feature in features if
                 (feature["start"], feature["end"], (method,)) not in existing]
        data["features"].extend(fresh)
        path = workdir / f"result{index}.gb"
        api.write_record(api.editor_to_record(data, original=record), path)
        results.append((item, path))
        job["log"] += f"{item['label']}: {len(rows)} {METHODS[method].lower()} found; {len(fresh)} new features.\n"
        api.step(job, 0, (index + 1) / len(items), f"{index + 1} of {len(items)} sequences")
    api.step(job, 1)
    api.annotated(job, workdir, results, "_repeats", into, apply, busy)
