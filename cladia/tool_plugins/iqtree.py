"""IQ-TREE engine for the core tree operation."""
import re
import shutil
from pathlib import Path
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "paths": [
            "/usr/local/bin/iqtree3"
        ],
        "estimateBytes": 20000000
    },
    "id": "iqtree", "label": "IQ-TREE", "blurb": "Maximum likelihood with model selection and ultrafast bootstrap support.",
    "supports": ["tree.build"], "order": 10, "lane": "compute", "cores": None, "jobKind": "tree",
    "options": [
        {"key": "model", "label": "Model", "type": "text", "default": "auto",
         "help": "Auto chooses a model; or enter a model such as GTR+F+I+G4."},
        {"key": "bootstrap", "label": "Ultrafast bootstrap (1000 replicates)", "type": "bool", "default": True},
        {"key": "outgroup", "label": "Outgroup", "type": "choice", "default": "",
         "choices": [{"value": "", "label": "None (unrooted)"}], "choicesFrom": {"slot": "alignment", "kind": "alignment_rows"}},
        {"key": "threads", "label": "Threads", "type": "text", "default": "AUTO",
         "help": "AUTO chooses from the alignment size; or enter a number."},
    ],
}


AUTO_FROM = 300e3

FIXED_THREADS = 4

def run(job, workdir, operation, items, output, add_to=None, model="auto", bootstrap=True,
        outgroup="", threads="AUTO", program="iqtree"):
    return run_tree(job, workdir, items[0], program, output, model=model,
                         bootstrap=bootstrap, outgroup=outgroup or None, threads=threads)


def _is_protein(records):
    """An alignment is protein when most of its letters aren't nucleotide codes."""
    return api.looks_protein("".join(str(r.seq) for r in records))

def patterns(records):
    """How many distinct columns an alignment has (IQ-TREE's 'distinct patterns')."""
    return len(set(zip(*(str(r.seq).upper() for r in records))))

def iqtree_threads(sequences, patterns, cores):
    """-T for IQ-TREE: a number, or AUTO for large alignments."""
    if sequences * patterns >= AUTO_FROM:
        return "AUTO"
    return str(max(1, min(cores, FIXED_THREADS)))

def run_tree(job, workdir, item, program, output, model='auto', bootstrap=True, outgroup=None, threads='AUTO'):
    program = 'iqtree'
    records = api.read_records(Path(item['path']), api.file_format(item['path']))
    used, renamed = (set(), {})
    for record in records:
        name = re.sub('[^A-Za-z0-9_.-]+', '_', record.id) or 'seq'
        while name in used:
            name += '_'
        used.add(name)
        renamed[record.id] = name
        record.id, record.description = (name, '')
    api.write_fasta(records, workdir / 'aln.fasta')
    protein = _is_protein(records)
    api.steps(job, *(['Choosing a model'] if model == 'auto' else []), 'Searching trees', 'Finishing')
    api.ensure_tools_image(job)
    job['progress']['detail'] = ''
    if str(threads).upper() == 'AUTO':
        distinct = patterns(records)
        threads = iqtree_threads(len(records), distinct, job.get('cores', 1))
        job['log'] += f'Threads: {threads} ({len(records)} sequences, {distinct:,} distinct patterns)\n'
    args = ['iqtree3', '-s', '/data/aln.fasta', '--prefix', '/data/run', '--seed', '1', '-m', 'MFP' if model == 'auto' else model, '-T', str(threads)]
    if str(threads).upper() == 'AUTO':
        args += ['--threads-max', str(job.get('cores', 1))]
    if bootstrap:
        args += ['-B', '1000']
    if outgroup:
        args += ['-o', renamed.get(outgroup, outgroup)]
    job['live_log'] = str(workdir / 'run.log')
    api.watch_log(job, workdir / "run.log", _watch_iqtree)
    api.run_container(job, api.tools_image(), args, workdir)
    job['log'] += (workdir / 'run.iqtree').read_text(encoding='utf-8', errors='replace')
    shutil.copyfile(workdir / 'run.treefile', workdir / 'tree.nwk')
    api.add_output(job, workdir / 'tree.nwk', output, kind='tree')

def _watch_iqtree(text, progress):
    """IQ-TREE's run.log: models tested of those it will test, then the tree
    search's iterations (it stops when the trees stop improving: no fraction)."""
    names = progress["steps"]
    if "FINALIZING TREE SEARCH" in text:
        return {"at": names.index("Finishing"), "fraction": None, "detail": ""}
    if "INITIALIZING CANDIDATE TREE SET" in text:
        iterations = re.findall(r"^Iteration (\d+) /", text, re.M)
        return {"at": names.index("Searching trees"), "fraction": None,
                "detail": f"iteration {iterations[-1]}" if iterations else ""}
    total = re.search(r"ModelFinder will test up to (\d+)", text)
    tested = len(re.findall(r"^\s*\d+\s+\S+\s+-?\d+\.\d+\s+\d+", text, re.M))
    if total and tested and "Choosing a model" in names:
        return {"at": 0, "fraction": min(1, tested / int(total.group(1))),
                "detail": f"{tested} of up to {total.group(1)} models"}
    if "Measuring multi-threading efficiency" in text:  # -T AUTO: test runs first (most of a small run)
        return {"at": 0, "fraction": None, "detail": "finding the best number of threads"}
    return None
