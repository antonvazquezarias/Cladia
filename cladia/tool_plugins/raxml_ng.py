"""RAxML-NG: maximum-likelihood trees with bootstrap support.

An optional tool: Settings â€º Tools â€º Install downloads RAxML-NG 2.0.4 from
bioconda into its own environment (conda.py; lock: locks/raxml_ng-2.0.4.lock).
With bootstrap replicates it runs --all (tree search, bootstrapping, supports
on the best tree); without, --search.
"""
import re

from cladia import engine_api as api

VERSION = "2.0.4"
ENGINE = {
    "id": "raxml_ng", "label": "RAxML-NG", "order": 15,
    "blurb": "Maximum likelihood with standard (Felsenstein) bootstrap support.",
    "supports": ["tree.build"], "optional": True, "lane": "compute", "cores": None, "jobKind": "raxml",
    "conda": {"version": VERSION, "spec": "raxml-ng=2.0.4"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/raxml_ng-*"],
                "estimateBytes": 161_000_000},
    "options": [
        {"key": "model", "label": "Model", "type": "text", "default": "auto",
         "help": "auto: GTR+G for DNA, LG+G for proteins; or a model such as GTR+I+G4, HKY+G or WAG+G."},
        {"key": "bootstrap", "label": "Bootstrap replicates", "type": "int", "default": 100, "min": 0, "max": 10000,
         "help": "0 for the best tree only, without supports."},
        {"key": "outgroup", "label": "Outgroup", "type": "choice", "default": "",
         "choices": [{"value": "", "label": "None (unrooted)"}], "choicesFrom": {"slot": "alignment", "kind": "alignment_rows"}},
    ],
}
CITATIONS = [{"tool": "RAxML-NG", "reference": "Kozlov et al. (2019), Bioinformatics 35:4453",
              "url": "https://doi.org/10.1093/bioinformatics/btz305"}]


def install(job, workdir, choices):
    api.conda_install(job, workdir, ENGINE)
    _version(job, workdir)


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)


def _version(job, workdir):
    text = api.run_container(job, api.tools_image(), api.conda_command(ENGINE, ["raxml-ng", "--version"]), workdir)
    found = re.search(r"RAxML-NG v\. (\S+)", text)
    if not found or found.group(1) != VERSION:
        raise RuntimeError(f"Expected RAxML-NG {VERSION}, got {text.strip()[:80]!r}; reinstall it in Settings â€º Tools")
    api.record_tool(job, "RAxML-NG", VERSION, CITATIONS)


def _watch(text, progress):
    """run.raxml.log: the ML searches (of N starting trees), then the bootstrap trees."""
    names = progress["steps"]
    boot = re.search(r"Starting bootstrapping analysis with (\d+) replicates", text)
    if boot and "Bootstrapping" in names:
        done = re.findall(r"Bootstrap tree #(\d+)", text)
        return {"at": names.index("Bootstrapping"), "fraction": int(done[-1]) / int(boot.group(1)) if done else 0,
                "detail": f"{done[-1] if done else 0} of {boot.group(1)} replicates"}
    starts = re.search(r"Starting ML tree search with (\d+) distinct starting trees", text)
    if starts:
        done = re.findall(r"ML tree search #(\d+)", text)
        return {"at": 0, "fraction": int(done[-1]) / int(starts.group(1)) if done else 0,
                "detail": f"{done[-1] if done else 0} of {starts.group(1)} starting trees"}
    return None


def run(job, workdir, operation, items, output, add_to=None, model="auto", bootstrap=100, outgroup="", program=None):
    if operation != "tree.build" or len(items) != 1:
        raise ValueError("RAxML-NG builds a tree from one alignment")
    if not isinstance(bootstrap, int) or isinstance(bootstrap, bool) or not 0 <= bootstrap <= 10000:
        raise ValueError("Bootstrap replicates must be between 0 and 10,000")
    model = (model or "auto").strip()
    if not re.fullmatch(r"[A-Za-z0-9+{}().,/_-]+", model):
        raise ValueError(f"Not a model RAxML-NG can read: {model}")
    records, renamed = api.tree_rows(items[0])
    protein = api.looks_protein("".join(str(r.seq) for r in records))
    if model == "auto":
        model = "LG+G" if protein else "GTR+G"
    api.write_fasta(records, workdir / "aln.fasta")
    api.steps(job, "Searching trees", *(["Bootstrapping"] if bootstrap else []), "Finishing")
    _version(job, workdir)
    job["log"] += f"Model {model}; {bootstrap or 'no'} bootstrap replicates\n"
    args = ["raxml-ng", "--all" if bootstrap else "--search", "--msa", "/data/aln.fasta", "--model", model,
            "--prefix", "/data/run", "--seed", "1", "--threads", f"auto{{{max(1, int(job.get('cores') or 1))}}}"]
    if bootstrap:
        args += ["--bs-trees", str(bootstrap)]
    if outgroup:
        args += ["--outgroup", renamed.get(outgroup, outgroup)]
    job["live_log"] = str(workdir / "run.raxml.log")
    api.watch_log(job, workdir / "run.raxml.log", _watch)
    api.run_container(job, api.tools_image(), api.conda_command(ENGINE, args), workdir)
    api.step(job, len(job["progress"]["steps"]) - 1)
    tree = workdir / ("run.raxml.support" if bootstrap else "run.raxml.bestTree")
    (workdir / "tree.nwk").write_text(tree.read_text(encoding="utf-8").strip() + "\n", encoding="utf-8")
    api.add_output(job, workdir / "tree.nwk", output, kind="tree")
