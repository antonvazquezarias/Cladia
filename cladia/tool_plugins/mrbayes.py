"""MrBayes: Bayesian trees with posterior probabilities.

An optional tool: Settings › Tools › Install downloads MrBayes 3.2.9 from bioconda
into its own environment (conda.py; lock: locks/mrbayes-3.2.9.lock). The job
writes the alignment as NEXUS with a MrBayes block (model, chains, sampling),
runs it in batch mode and saves the majority-rule consensus tree, its posterior
probabilities as supports (0-1). The convergence numbers go in the log.
"""
import re

from cladia import engine_api as api

VERSION = "3.2.9"
MODELS = {
    "gtr_ig": ("GTR+I+G", "lset nst=6 rates=invgamma;"),
    "gtr_g": ("GTR+G", "lset nst=6 rates=gamma;"),
    "hky_g": ("HKY+G", "lset nst=2 rates=gamma;"),
    "jc": ("JC", "lset nst=1 rates=equal;"),
    "mixed": ("Averaged over substitution models (nst=mixed) +I+G", "lset nst=mixed rates=invgamma;"),
}
PROTEIN_MODEL = "prset aamodelpr=mixed; lset rates=gamma;"
ENGINE = {
    "id": "mrbayes", "label": "MrBayes", "order": 30,
    "blurb": "Bayesian inference: a consensus tree with posterior probabilities. Runs can take hours.",
    "supports": ["tree.build"], "optional": True, "lane": "compute", "cores": 1, "jobKind": "mrbayes",
    "conda": {"version": VERSION, "spec": "mrbayes=3.2.9"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/mrbayes-*"],
                "estimateBytes": 561_000_000},
    "options": [
        {"key": "model", "label": "DNA model", "type": "choice", "default": "gtr_ig",
         "choices": [{"value": key, "label": label} for key, (label, _) in MODELS.items()],
         "help": "Proteins always average over the fixed amino acid models, with gamma rates."},
        {"key": "generations", "label": "Generations", "type": "int", "default": 1_000_000, "min": 10_000,
         "max": 100_000_000},
        {"key": "outgroup", "label": "Outgroup", "type": "choice", "default": "",
         "choices": [{"value": "", "label": "None (the first sequence)"}],
         "choicesFrom": {"slot": "alignment", "kind": "alignment_rows"}},
        {"key": "sample_every", "label": "Sample every (generations)", "type": "int", "default": 1000, "min": 10,
         "advanced": True},
        {"key": "runs", "label": "Independent runs", "type": "int", "default": 2, "min": 1, "max": 4, "advanced": True},
        {"key": "chains", "label": "Chains per run", "type": "int", "default": 4, "min": 1, "max": 8, "advanced": True},
        {"key": "burnin", "label": "Burn-in (% of samples discarded)", "type": "int", "default": 25, "min": 0,
         "max": 90, "advanced": True},
    ],
}
CITATIONS = [{"tool": "MrBayes", "reference": "Ronquist et al. (2012), Systematic Biology 61:539",
              "url": "https://doi.org/10.1093/sysbio/sys029"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    (workdir / "version.nex").write_text("begin mrbayes; quit; end;\n", encoding="utf-8")
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["mb", "/data/version.nex"]), workdir)
    found = re.search(r"MrBayes\s+v?(\d+\.\d+\.\d+)", text)
    if not found or found.group(1) != VERSION:
        raise RuntimeError(f"Expected MrBayes {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings › Tools")
    api.record_tool(job, "MrBayes", found.group(1), CITATIONS)


def block(model, protein, generations, sample_every, runs, chains, burnin, outgroup=None):
    """The MrBayes block: settings, model, the run, then the summaries."""
    lines = ["begin mrbayes;", "  set autoclose=yes nowarn=yes quitonerror=yes seed=1 swapseed=1;"]
    if outgroup:
        lines.append(f"  outgroup {outgroup};")
    lines.append("  " + (PROTEIN_MODEL if protein else MODELS[model][1]))
    lines.append(f"  mcmcp ngen={generations} samplefreq={sample_every} printfreq={max(sample_every, generations // 200)} "
                 f"diagnfreq={max(sample_every, generations // 50)} nruns={runs} nchains={chains} "
                 f"relburnin=yes burninfrac={burnin / 100:g};")
    lines += ["  mcmc;", f"  sump relburnin=yes burninfrac={burnin / 100:g};",
              f"  sumt relburnin=yes burninfrac={burnin / 100:g};", "end;"]
    return "\n".join(lines) + "\n"


def _watch(text, progress):
    """MrBayes prints a line per printfreq generations: "  12000 -- (-2345.6) ..."."""
    done = re.findall(r"^\s*(\d+) -- ", text, re.M)
    if "Summarizing" in text or "Summary statistics" in text:
        return {"at": 1, "fraction": None, "detail": "Summarising the samples"}
    if not done:
        return None
    total = progress.get("generations") or 1
    return {"at": 0, "fraction": min(1, int(done[-1]) / total), "detail": f"generation {int(done[-1]):,} of {total:,}"}


def run(job, workdir, operation, items, output, add_to=None, model="gtr_ig", generations=1_000_000,
        outgroup="", sample_every=1000, runs=2, chains=4, burnin=25, program=None):
    if operation != "tree.build" or len(items) != 1:
        raise ValueError("MrBayes builds a tree from one alignment")
    if model not in MODELS:
        raise ValueError("Choose a DNA model")
    numbers = {"generations": generations, "sample_every": sample_every, "runs": runs, "chains": chains, "burnin": burnin}
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in numbers.values()):
        raise ValueError("MrBayes settings must be whole numbers")
    if sample_every > generations or generations // sample_every < 10:
        raise ValueError("Sample at least ten times: fewer generations between samples, or more generations")
    if not 1 <= runs <= 4 or not 1 <= chains <= 8 or not 0 <= burnin <= 90:
        raise ValueError("Runs 1-4, chains 1-8 and burn-in 0-90 %")
    records, renamed = api.tree_rows(items[0], r"[^A-Za-z0-9_]+")  # NEXUS: no dots or dashes in names
    protein = api.looks_protein("".join(str(r.seq) for r in records))
    data = api.nexus_alignment(records, protein)
    (workdir / "aln.nex").write_text(data + "\n" + block(model, protein, generations, sample_every, runs, chains,
                                                          burnin, renamed.get(outgroup) if outgroup else None),
                                     encoding="utf-8")
    job["log"] += (f"Model {'mixed amino acid models +G' if protein else MODELS[model][0]}; {generations:,} generations, "
                   f"{runs} runs of {chains} chains, a sample every {sample_every:,}, {burnin} % burn-in\n")
    api.steps(job, "Sampling trees", "Summarising")
    job["progress"]["generations"] = generations
    _version(job, workdir)
    job["live_log"] = str(workdir / "run.log")
    api.tool_watched(job, api.conda_command(ENGINE, ["mb", "/data/aln.nex"]), workdir, _watch, stdout=True)
    log = (workdir / "run.log").read_text(encoding="utf-8", errors="replace")
    split = re.findall(r"Average standard deviation of split frequencies:\s*([\d.]+)", log)
    if split:
        job["log"] += (f"Average standard deviation of split frequencies at the end: {split[-1]}"
                       f"{' (above 0.01: the runs may not have converged; consider more generations)' if float(split[-1]) > 0.01 else ''}\n")
    consensus = workdir / "aln.nex.con.tre"
    if not consensus.exists():
        raise RuntimeError("MrBayes wrote no consensus tree; see its log")
    (workdir / "tree.nwk").write_text(api.newick_from_nexus(consensus.read_text(encoding="utf-8")), encoding="utf-8")
    api.add_output(job, workdir / "tree.nwk", output, kind="tree")
