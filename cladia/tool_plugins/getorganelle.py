"""GetOrganelle: an organelle genome assembled from one sample's Illumina reads.

An optional tool: Settings › Tools › Install downloads GetOrganelle 1.7.7.1 from
bioconda (with SPAdes, Bowtie2 and BLAST+; lock: locks/getorganelle-1.7.7.1.lock)
into its own environment in Cladia's Linux, then the seed and label databases
chosen (installChoices; more can be added later). The databases live in a volume
of this data folder's own, mounted at DB_PATH.

GetOrganelle's own log (assembly/get_org.log.txt) is the job's live log; the job
finisher appends it once, so the engine doesn't.
"""
import re

from cladia import engine_api as api

VERSION = "1.7.7.1"
DATABASES = [
    {"value": "other_pt", "label": "Other plastids (including algae)"},
    {"value": "embplant_pt", "label": "Land-plant plastids"},
    {"value": "embplant_mt", "label": "Land-plant mitochondria"},
    {"value": "embplant_nr", "label": "Land-plant nuclear ribosomal DNA"},
    {"value": "animal_mt", "label": "Animal mitochondria"},
    {"value": "fungus_mt", "label": "Fungal mitochondria"},
    {"value": "fungus_nr", "label": "Fungal nuclear ribosomal DNA"},
]
DB_PATH = "/organelle-db"

ENGINE = {
    "id": "getorganelle", "label": "GetOrganelle",
    "blurb": "Assemble an organelle genome from one sample's Illumina reads.",
    "supports": ["assemble.organelle"], "optional": True, "installChoices": DATABASES,
    "conda": {"version": VERSION, "spec": "getorganelle=1.7.7.1"},
    "storage": {"kind": "linux", "environment": "tools", "paths": ["/opt/cladia-conda/envs/getorganelle-*"],
                "estimateBytes": 1_400_000_000,
                "note": "Brings SPAdes, Bowtie2 and BLAST+; the databases you choose come on top (their sizes vary)."},
    "order": 20, "lane": "compute", "cores": None, "jobKind": "getorganelle",
    "options": [
        {"key": "organelle_type", "label": "Target", "type": "choice", "default": "other_pt",
         "choices": DATABASES, "choicesFromInstall": True},
        {"key": "max_rounds", "label": "Maximum read-extension rounds", "type": "int", "default": 15,
         "min": 1, "max": 100, "advanced": True},
        {"key": "kmers", "label": "SPAdes k-mers", "type": "text", "default": "21,55,85,115",
         "advanced": True, "help": "Odd values under the read length, separated by commas."},
    ],
}


def _volume():
    return api.private_volume("getorganelle-databases")


def install(job, workdir, choices):
    """The environment (again only when its lock changed), then the chosen databases."""
    api.conda_install(job, workdir, ENGINE)
    ok, detail = api.volume_create(_volume())
    if not ok:
        raise RuntimeError(f"Could not create the GetOrganelle database store: {detail}")
    for index, organelle in enumerate(choices):
        api.step(job, job["progress"]["at"], index / max(1, len(choices)), f"Database: {organelle}")
        api.run_container(job, api.tools_image(),
                          api.conda_command(ENGINE, ["get_organelle_config.py", "--add", organelle, "--config-dir", DB_PATH]),
                          workdir, mounts={f"volume:{_volume()}": DB_PATH})
    api.step(job, job["progress"]["at"], 1, "Databases ready")


def uninstall(job, workdir):
    api.conda_uninstall(job, workdir, ENGINE)
    api.volume_remove(_volume())


def run(job, workdir, operation, items, output, add_to=None, groups=None,
        organelle_type="other_pt", max_rounds=15, kmers="21,55,85,115"):
    if operation != "assemble.organelle" or len(groups or []) != 1:
        raise ValueError("Choose one sample's paired or single-end Illumina reads")
    if not all(part.isdigit() and int(part) % 2 and 15 <= int(part) <= 255 for part in kmers.split(",")):
        raise ValueError("SPAdes k-mers must be odd numbers between 15 and 255")
    api.steps(job, "Recruiting and assembling reads", "Saving the assembly")
    mounts, inside = api.read_mounts(groups)
    mounts[f"volume:{_volume()}"] = DB_PATH
    reads = groups[0]["files"]
    args = ["get_organelle_from_reads.py", "-F", organelle_type, "-o", "/data/assembly",
            "-t", str(job["cores"]), "-R", str(max_rounds), "-k", kmers, "--config-dir", DB_PATH]
    args += ["-1", inside[reads[0]], "-2", inside[reads[1]]] if len(reads) == 2 else ["-u", inside[reads[0]]]
    result = workdir / "assembly"
    api.watch_log(job, result / "get_org.log.txt", _watch_getorganelle)
    job["log"] += "GetOrganelle started; its live log appears below as reads are processed.\n"
    api.run_container(job, api.tools_image(), api.conda_command(ENGINE, args), workdir, mounts=mounts)
    paths = sorted(result.glob("*path_sequence.fasta"))
    if not paths:
        raise RuntimeError("GetOrganelle produced no path sequences; see its log for assembly details")
    api.step(job, 1, 0, f"{len(paths)} sequence result(s)")
    for n, path in enumerate(paths):
        name = output if len(paths) == 1 else f"{output} {n + 1}"
        api.add_output(job, path, name)
        api.step(job, 1, (n + 1) / len(paths), name)
    for n, path in enumerate(sorted(result.glob("*selected_graph.gfa"))):
        api.add_output(job, path, f"{output} graph {n + 1}", kind="graph")


def _watch_getorganelle(text, progress):
    """Show the latest completed GetOrganelle message without inventing a fraction."""
    messages = re.findall(r"^\S[^\r\n]*? - (?:INFO|WARNING|ERROR): (.+)$", text, re.M)
    return {"at": 0, "fraction": None, "detail": messages[-1].strip()[:160]} if messages else None
