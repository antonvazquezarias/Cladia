"""Real check of the optional tools from bioconda, in Cladia's own Linux.

    python scripts/check_conda_tools.py [clustal_omega krait getorganelle muscle macse raxml_ng mrbayes trnascan aragorn trimming]

A private temporary library: each tool is installed through the same install job
Settings › Tools runs (downloads from bioconda), used once, then uninstalled, and
its environment checked gone. Prints a line per check; exits non-zero on failure.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
scratch = Path(tempfile.mkdtemp(prefix="cladia-conda-check-"))
os.environ["CLADIA_DATA"] = str(scratch / "data")
sys.path.insert(0, str(ROOT))

from cladia import config, conda, jobs, library, linux, tool_install  # noqa: E402
from cladia.tool_registry import build_registry, invalidate_registry  # noqa: E402

config.DATA_DIR = scratch / "data"
config.APP_DIR = config.DATA_DIR / ".cladia"
library.ensure_folders()


def wait(job, limit=3600):
    deadline = time.time() + limit
    while jobs.JOBS[job["id"]]["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(1)
    job = jobs.JOBS[job["id"]]
    if job["status"] != "done":
        sys.exit(f"{job['title']}: {job['status']}\n{job.get('error')}\n{job['log'][-3000:]}")
    return job


def env_exists(engine_id, version):
    proc = linux._host("sh", "-c", f"test -f {conda.env_path(engine_id, version)}/.cladia-lock && echo yes || echo no")
    return proc.stdout.strip() == "yes"


def install(engine_id, choices=()):
    started = time.time()
    wait(tool_install.install_engines([{"id": engine_id, "choices": list(choices)}]))
    invalidate_registry()
    engine = build_registry().engines[engine_id]
    assert build_registry().available(engine), f"{engine_id} not available after install"
    assert env_exists(engine_id, engine["conda"]["version"]), f"{engine_id}: no environment"
    print(f"installed {engine_id} in {time.time() - started:.0f} s")
    return engine


def uninstall(engine_id, version):
    wait(tool_install.uninstall_engine(engine_id))
    invalidate_registry()
    assert not build_registry().available(build_registry().engines[engine_id])
    assert not env_exists(engine_id, version), f"{engine_id}: environment left behind"
    print(f"uninstalled {engine_id}")


def run(engine, operation, items, **options):
    job = jobs.submit(engine.get("jobKind", engine["id"]), f"check {engine['id']}", engine["run"],
                      resources=(engine["lane"], engine["cores"]), operation=operation, items=items, output="check", **options)
    return wait(job)


def fasta(name, text):
    path = scratch / f"{name}.fa"
    path.write_text(text, encoding="ascii")
    ids, errors = library.import_paths([path])
    assert not errors, errors
    return [library.get_item(i) for i in ids] + [library.get_item(c["id"]) for c in library.items() if c.get("parent") in ids]


def check_clustal():
    engine = install("clustal_omega")
    rows = [it for it in fasta("three", ">a\nACGTACGTTAGCAT\n>b\nACGTACGATAGCAT\n>c\nACGTCGTTAGCAT\n") if it["kind"] == "sequence"]
    job = run(engine, "align.new", rows)
    aligned = library.records(library.get_item(job["outputs"][0]["id"]))
    assert [r.id for r in aligned] == ["a", "b", "c"] and len({len(r.seq) for r in aligned}) == 1
    assert "Clustal Omega" in job["log"] and "1.2.4" in job["log"]
    print("clustal_omega: aligned 3 rows, input order kept")
    uninstall("clustal_omega", "1.2.4")


def check_krait():
    engine = install("krait")
    samples = [("perfect", "AC" * 10 + "GT", {}),
               ("compound", "AC" * 7 + "GGG" + "ATG" * 5 + "TC", {"max_distance": 3}),
               ("imperfect", "AC" * 6 + "AT" + "AC" * 6 + "GT", {}),
               ("generic", "GATTACA" * 4 + "CG", {"gtr_min_motif": 7, "gtr_max_motif": 7, "gtr_min_repeats": 3, "gtr_min_length": 21})]
    for method, sequence, settings in samples:
        (item,) = fasta(method, f">{method}\n{sequence}\n")
        job = run(engine, "variants.microsatellites", [item], method=method, apply=False, **settings)
        record = library.records(library.get_item(job["outputs"][0]["id"]))[0]
        found = [f for f in record.features if f.type == "repeat_region" and f.qualifiers.get("krait_method") == [method]]
        assert found, (method, job["log"][-1500:])
        print(f"krait: {method} found {len(found)} repeat(s)")
    uninstall("krait", "1.5.0")


def check_getorganelle():
    engine = install("getorganelle", ["other_pt"])
    proc = linux._host("sh", "-c",
                     f"PATH={conda.env_path('getorganelle', '1.7.7.1')}/bin:/usr/bin:/bin get_organelle_from_reads.py --version")
    assert "1.7.7.1" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    vol = f"{linux.VOLUMES}/cladia-getorganelle-databases-{conda.data_key()}"
    listed = linux._host("sh", "-c", f"ls {vol}").stdout
    assert listed.strip(), "no database files"
    print(f"getorganelle: version ok, other_pt database in place ({' '.join(listed.split()[:4])}…)")
    uninstall("getorganelle", "1.7.7.1")
    gone = linux._host("sh", "-c", f"test -e {vol} && echo left || echo gone").stdout.strip()
    assert gone == "gone", "database volume left behind"


# ── Alignment, tree, tRNA and trimming tools (synthetic data) ──

def _random_gene(seed, codons=120):
    import random
    rng = random.Random(seed)
    return "ATG" + "".join(rng.choice("ACGT") + rng.choice("ACGT") + rng.choice("ACGT") for _ in range(codons)) + "TAA"


def _mutants(gene, n=6, per=12, seed=5):
    import random
    rng = random.Random(seed)
    out = []
    for k in range(n):
        s = list(gene)
        for _ in range(per):
            i = rng.randrange(3, len(s) - 3)
            s[i] = rng.choice("ACGT")
        out.append("".join(s))
    return out


def _gene_rows(name, genes):
    text = "".join(f">{name}{k}\n{g}\n" for k, g in enumerate(genes))
    return [it for it in fasta(name, text) if it["kind"] == "sequence"]


def _aligned(job):
    return library.records(library.get_item(job["outputs"][0]["id"]))


def check_muscle():
    engine = install("muscle")
    rows = _gene_rows("mu", _mutants(_random_gene(1)))
    job = run(engine, "align.new", rows)
    aligned = _aligned(job)
    assert [r.id for r in aligned] == [r["name"] for r in rows] and len({len(r.seq) for r in aligned}) == 1
    print(f"muscle: aligned {len(aligned)} rows, input order kept")
    uninstall("muscle", "5.3")


def check_macse():
    engine = install("macse")
    gene = _random_gene(2)
    genes = _mutants(gene, 4) + [gene[:60] + gene[63:]]  # the last lacks one codon
    rows = _gene_rows("ma", genes)
    job = run(engine, "align.new", rows, code=1)
    aligned = _aligned(job)
    assert len(aligned) == 5 and len({len(r.seq) for r in aligned}) == 1 and len(aligned[0].seq) % 3 == 0
    print(f"macse: aligned {len(aligned)} coding rows by codon ({len(aligned[0].seq)} columns); {len(job['outputs'])} results")
    uninstall("macse", "2.07")


def _alignment_item(genes):
    path = scratch / "aln.fa"
    path.write_text("".join(f">t{k}\n{g}\n" for k, g in enumerate(genes)), encoding="ascii")
    return library.get_item(library.add_output(path, "aln", kind="alignment"))


def _tree_tips(job):
    from Bio import Phylo
    from io import StringIO
    item = library.get_item(job["outputs"][0]["id"])
    return sorted(t.name for t in Phylo.read(StringIO(Path(item["path"]).read_text()), "newick").get_terminals())


def check_raxml():
    engine = install("raxml_ng")
    job = run(engine, "tree.build", [_alignment_item(_mutants(_random_gene(3)))], bootstrap=20)
    assert _tree_tips(job) == [f"t{k}" for k in range(6)]
    print("raxml_ng: tree with 6 tips and bootstrap supports")
    uninstall("raxml_ng", "2.0.4")


def check_mrbayes():
    engine = install("mrbayes")
    job = run(engine, "tree.build", [_alignment_item(_mutants(_random_gene(4)))], generations=20000, sample_every=100,
              runs=2, chains=2)
    assert _tree_tips(job) == [f"t{k}" for k in range(6)]
    print("mrbayes: consensus tree with 6 tips; " + [l for l in job["log"].splitlines() if "split frequencies" in l][-1])
    uninstall("mrbayes", "3.2.9")


def check_trna():
    # E. coli tRNA-Leu (CAG) gene, 87 bp, inside random flanks
    leu = "GCGAAGGTGGCGGAATTGGTAGACGCGCTAGCTTCAGGTGTTAGTGTCCTTACGGACGTGGGGGTTCAAGTCCCCCCCCTCGCACCA"
    flank = _random_gene(6, 80)
    (item,) = fasta("trna", f">withtrna\n{flank}{leu}{flank}\n")
    engine = install("trnascan")
    job = run(engine, "annotate.trna", [item], model="bacterial", apply=False)
    record = library.records(library.get_item(job["outputs"][0]["id"]))[0]
    found = [f for f in record.features if f.type == "tRNA"]
    assert found and "tRNA-Leu" in found[0].qualifiers["product"][0], job["log"][-800:]
    print(f"trnascan: {found[0].qualifiers['gene'][0]} {found[0].qualifiers['product'][0]} at {int(found[0].location.start) + 1}-{int(found[0].location.end)}")
    uninstall("trnascan", "2.0.13")


def check_aragorn():
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio import SeqIO
    leu = "GCGAAGGTGGCGGAATTGGTAGACGCGCTAGCTTCAGGTGTTAGTGTCCTTACGGACGTGGGGGTTCAAGTCCCCCCCCTCGCACCA"
    flank = _random_gene(6, 80)
    (linear,) = fasta("aragorn", f">withtrna\n{flank}{leu}{flank}\n")
    # a circle whose origin falls inside the tRNA (its last 40 bases come first)
    path = scratch / "circle.gb"
    SeqIO.write(SeqRecord(Seq(leu[-40:] + flank + flank + leu[:-40]), id="circle", name="circle",
                          annotations={"molecule_type": "DNA", "topology": "circular"}), path, "genbank")
    (circle_id,), errors = library.import_paths([path])
    assert not errors, errors
    engine = install("aragorn")
    for item in (linear, library.get_item(circle_id)):
        job = run(engine, "annotate.trna", [item], apply=False)
        record = library.records(library.get_item(job["outputs"][0]["id"]))[0]
        found = [f for f in record.features if f.type == "tRNA"]
        assert found and found[0].qualifiers["product"] == ["tRNA-Leu"], job["log"][-1500:]
        parts = [(int(p.start) + 1, int(p.end)) for p in found[0].location.parts]
        print(f"aragorn: {item['label']}: {found[0].qualifiers['gene'][0]} {found[0].qualifiers['product'][0]} at {parts}")
        assert sum(e - s + 1 for s, e in parts) == len(leu)
    uninstall("aragorn", "1.2.41")


def check_trimming():
    import random
    rng = random.Random(8)
    core = _random_gene(9, 100)
    genes = []
    for k in range(5):  # gappy ends: the first and last few columns of each row are gaps
        lead, tail = rng.randrange(0, 60), rng.randrange(0, 60)
        genes.append("-" * lead + core[lead:len(core) - tail] + "-" * tail)
    genes[0] = core
    for name, version, options in (("trimal", "1.5.1", {"method": "gappyout"}), ("clipkit", "2.14.0", {"mode": "smart-gap"})):
        engine = install(name)
        job = run(engine, "align.trim", [_alignment_item(genes)], **options)
        aligned = _aligned(job)
        assert len(aligned) == 5 and 0 < len(aligned[0].seq) < len(core), len(aligned[0].seq)
        print(f"{name}: {len(core)} -> {len(aligned[0].seq)} columns, 5 rows kept")
        uninstall(name, version)


CHECKS = {"clustal_omega": check_clustal, "krait": check_krait, "getorganelle": check_getorganelle,
          "muscle": check_muscle, "macse": check_macse, "raxml_ng": check_raxml, "mrbayes": check_mrbayes,
          "trnascan": check_trna, "aragorn": check_aragorn, "trimming": check_trimming}
for name in sys.argv[1:] or CHECKS:
    CHECKS[name]()
print("All checks passed; scratch library", scratch)
