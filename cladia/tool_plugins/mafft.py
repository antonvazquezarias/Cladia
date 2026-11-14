"""MAFFT alignment engine."""
import re
from pathlib import Path
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "paths": [
            "/usr/local/libexec/mafft",
            "/usr/local/bin/mafft*",
            "/usr/local/bin/einsi",
            "/usr/local/bin/ginsi",
            "/usr/local/bin/linsi",
            "/usr/local/bin/fftns*",
            "/usr/local/bin/nwns*"
        ],
        "estimateBytes": 15000000
    },
    "id": "mafft", "label": "MAFFT", "blurb": "Multiple sequence alignment for genes and genomes with the same gene order.",
    "supports": ["align.new", "align.add"], "order": 10, "lane": "compute", "cores": None, "jobKind": "mafft",
    "options": [
        {"key": "strategy", "label": "Strategy", "type": "choice", "default": "auto", "choices": [
            {"value": "auto", "label": "Auto (MAFFT picks by data size)"},
            {"value": "linsi", "label": "L-INS-i (most accurate, up to ~200 sequences)"},
            {"value": "ginsi", "label": "G-INS-i (similar-length sequences)"},
            {"value": "einsi", "label": "E-INS-i (long unalignable regions)"},
            {"value": "fftnsi", "label": "FFT-NS-i (iterative, medium-size data)"},
            {"value": "fftns2", "label": "FFT-NS-2 (fast, many sequences)"},
            {"value": "fftns1", "label": "FFT-NS-1 (fastest, rough)"},
            {"value": "nwnsi", "label": "NW-NS-i (like FFT-NS-i, without FFT)"},
        ]},
        {"key": "adjust_direction", "label": "Reverse-complement sequences in the wrong orientation", "type": "bool", "default": True, "dnaOnly": True},
        {"key": "fragments", "label": "The new sequences are fragments (--addfragments)", "type": "bool", "default": False,
         "defaultWhen": {"slot": "add_to", "kind": "sequence", "value": True}, "operations": ["align.add"]},
        {"key": "keep_length", "label": "Keep the alignment's (or reference's) length", "type": "bool", "default": False,
         "defaultWhen": {"slot": "add_to", "kind": "sequence", "value": True}, "operations": ["align.add"]},
        {"key": "max_iterate", "label": "Max iterations", "type": "int", "default": None, "min": 0, "advanced": True},
        {"key": "gap_open", "label": "Gap opening penalty (--op)", "type": "float", "default": None, "min": 0, "advanced": True},
        {"key": "gap_extend", "label": "Gap extension penalty (--ep)", "type": "float", "default": None, "min": 0, "advanced": True},
        {"key": "leave_gappy", "label": "Leave gappy regions unaligned (--leavegappyregion)", "type": "bool", "default": False, "advanced": True},
    ],
}


def run(job, workdir, operation, items, output, add_to=None, **options):
    return run_alignment(job, workdir, items, output, add_to=add_to, **options)


def run_alignment(job, workdir, items, output, strategy, adjust_direction, max_iterate=None,
                  gap_open=None, gap_extend=None, leave_gappy=False, add_to=None,
                  fragments=False, keep_length=False):
    # add_to: an alignment, or a single sequence used as a one-sequence reference
    # alignment. Sequences being added keep clear of the names already in it.
    used = set()
    sources = {}  # row name -> the library sequence it is (for the alignment view's features)
    if not add_to:
        existing = []
    elif add_to["kind"] == "alignment":
        existing = api.read_records(Path(add_to["path"]), api.file_format(add_to["path"]))
        used = {r.id for r in existing}
        sources = dict(add_to.get("sources") or {})
    else:
        existing = [api.named_record(add_to, used)]
        sources[existing[0].id] = {"item": add_to["id"]}
    records = []
    for item in items:
        records.append(api.named_record(item, used))
        sources[records[-1].id] = {"item": item["id"]}
    api.write_fasta(records, workdir / "input.fasta")

    args = ["mafft", "--thread", str(job.get("cores", 1)), *MAFFT_STRATEGIES[strategy]]
    if max_iterate is not None:
        api.set_option(args, "--maxiterate", max_iterate)
    if gap_open is not None:
        api.set_option(args, "--op", gap_open)
    if gap_extend is not None:
        api.set_option(args, "--ep", gap_extend)
    if leave_gappy:
        args.append("--leavegappyregion")
    if adjust_direction:
        args.append("--adjustdirection")  # reversed sequences get an _R_ prefix

    if add_to:  # the new sequences go into the existing alignment
        api.write_fasta(existing, workdir / "existing.fasta")
        args += ["--addfragments" if fragments else "--add", "/data/input.fasta"]
        if keep_length:
            args.append("--keeplength")  # insertions in the new sequences are dropped
        args.append("/data/existing.fasta")
    else:
        args.append("/data/input.fasta")

    # its stages, followed in its messages (_watch_mafft)
    api.steps(job, *mafft_steps(adjust_direction))
    job["progress"]["mafft"] = {"directions": adjust_direction, "add": bool(add_to)}
    api.ensure_tools_image(job)
    job["progress"]["detail"] = ""
    aligned = api.tool_watched(job, args, workdir, _watch_mafft)
    api.step(job, len(job["progress"]["steps"]) - 1, detail="Saving alignment")

    (workdir / "aligned.fasta").write_text(aligned, encoding="utf-8")
    api.add_output(job, workdir / "aligned.fasta", output, kind="alignment", extra={"sources": sources})


MAFFT_STRATEGIES = {
    "auto": ["--auto"],
    "linsi": ["--localpair", "--maxiterate", "1000"],             # L-INS-i: accurate, <200 seqs
    "ginsi": ["--globalpair", "--maxiterate", "1000"],            # G-INS-i: similar lengths
    "einsi": ["--genafpair", "--ep", "0", "--maxiterate", "1000"],  # E-INS-i: long unalignable regions
    "fftnsi": ["--retree", "2", "--maxiterate", "1000"],          # FFT-NS-i: iterative, medium size
    "fftns2": ["--retree", "2"],                                  # FFT-NS-2: fast, many seqs
    "fftns1": ["--retree", "1"],                                  # FFT-NS-1: fastest, rough
    "nwnsi": ["--retree", "2", "--maxiterate", "2", "--nofft"],   # NW-NS-i: like FFT-NS-i, no FFT
}

MAFFT_MARKERS = [("All-to-all alignment", "Aligning all pairs"), ("Making a distance matrix", "Distances"),
                 ("Constructing a UPGMA tree", "Guide tree"), ("Progressive alignment", "Aligning")]

def mafft_steps(directions=False):
    return (["Checking directions"] if directions else []) + ["Preparing alignment", "Aligning", "Finishing alignment"]

def _watch_mafft(text, progress):
    plan = progress["mafft"]
    names = mafft_steps(plan["directions"])
    offset = int(plan["directions"])
    events = []
    for marker, stage in MAFFT_MARKERS:
        events += [(m.start(), stage) for m in re.finditer(re.escape(marker), text)]
    # --addfragments may never print "Progressive alignment" at all.
    first_step = re.search(r"(?:^|[\r\n])[ \t]*STEP[ \t]+\d+[ \t]*/[ \t]*\d+", text)
    if first_step:
        events.append((first_step.start(), "Aligning to reference" if plan.get("add") else "Aligning"))
    refinement = re.search(r"(?:^|[\r\n])[ \t]*\d{3,}-\d{4,}-[01]\b", text)
    if refinement:
        events.append((refinement.start(), "Refining"))
    events += [(m.start(), "Combining results") for m in re.finditer(r"(?m)^[ \t]*Combining[ .]*", text)]
    events += [(m.start(), "Saving alignment") for m in re.finditer(r"(?m)^[ \t]*Strategy:", text)]
    if not events:
        return None
    events.sort()
    position, stage = events[-1]
    aligning = {"Aligning", "Aligning to reference", "Refining"}
    at = 2 if stage in {"Combining results", "Saving alignment"} else int(any(name in aligning for _, name in events))
    after = text[position:]
    fraction, detail = None, stage
    if stage == "Refining":
        passes = re.findall(r"(?:^|[\r\n])[ \t]*(\d{3,})-\d{4,}-[01]\b", after)
        if passes:
            detail += f" · pass {max(map(int, passes))}"
    elif stage not in {"Combining results", "Saving alignment"}:
        pattern = (r"STEP[ \t]+(\d+)[ \t]*/[ \t]*(\d+)" if stage in aligning else
                   r"(?:^|[\r\n])[ \t]*(\d+)[ \t]*/[ \t]*(\d+)(?=[ \t]*(?:\(|[\r\n]|$))")
        counts = [(int(a), int(b)) for a, b in re.findall(pattern, after) if int(b) > 0 and int(a) <= int(b)]
        if counts:
            total = counts[-1][1]
            done = max(a for a, b in counts if b == total)  # workers can report out of order
            # These are MAFFT work indices, not a count of finished input sequences.
            fraction = done / total
            detail += f" · {done} of {total} work units"
    return {"steps": names, "at": offset + at, "fraction": fraction, "detail": detail}
