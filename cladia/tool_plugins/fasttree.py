"""FastTree engine for the core tree operation."""
import re
from pathlib import Path
from cladia import engine_api as api

ENGINE = {
    "storage": {
        "kind": "linux",
        "environment": "tools",
        "paths": [
            "/usr/local/bin/FastTree*"
        ],
        "estimateBytes": 1000000
    },
    "id": "fasttree", "label": "FastTree", "blurb": "Approximate maximum likelihood and local support values, usually quicker for a first look.",
    "supports": ["tree.build"], "order": 20, "lane": "compute", "cores": None, "jobKind": "tree",
    "options": [],
}


FASTTREE_STAGES = ["Starting tree", "Minimum-evolution rounds", "Maximum-likelihood rounds", "Lengths and support"]

def run(job, workdir, operation, items, output, add_to=None, program="fasttree"):
    return run_tree(job, workdir, items[0], program, output)


def _is_protein(records):
    """An alignment is protein when most of its letters aren't nucleotide codes."""
    return api.looks_protein("".join(str(r.seq) for r in records))

def run_tree(job, workdir, item, program, output, model='auto', bootstrap=True, outgroup=None, threads='AUTO'):
    program = 'fasttree'
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
    api.steps(job, *FASTTREE_STAGES)
    api.ensure_tools_image(job)
    job['progress']['detail'] = ''
    args = ['env', f"OMP_NUM_THREADS={job.get('cores', 1)}", 'fasttreeMP', *(['-lg'] if protein else ['-nt', '-gtr']), '-gamma', '/data/aln.fasta']
    (workdir / 'tree.nwk').write_text(api.tool_watched(job, args, workdir, _watch_fasttree), encoding='utf-8')
    api.add_output(job, workdir / 'tree.nwk', output, kind='tree')

def _watch_fasttree(text, progress):
    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]
    at, since = 0, 0  # the stage, and the line it began at
    for i, l in enumerate(lines):
        stage = (1 if l.startswith(("Initial topology", "Refining topology")) else
                 2 if l.startswith(("ML-NNI round", "ML Lengths")) or "Optimizing GTR model" in l
                 or re.match(r"[\d.]+ seconds: ML ", l) else  # its progress lines come before the round's summary
                 3 if l.startswith(("Optimize all lengths", "Gamma(")) else None)
        if stage is not None and stage > at:
            at, since = stage, i
    news = [l for l in lines[since:] if re.match(r"[\d.]+ seconds: ", l)]
    detail = re.sub(r"^[\d.]+ seconds: ", "", news[-1]) if news else ""
    joined = re.search(r"Joined\s+(\d+) of\s+(\d+)", detail)
    fraction = int(joined.group(1)) / int(joined.group(2)) if at == 0 and joined else None
    return {"at": at, "fraction": fraction, "detail": detail}
