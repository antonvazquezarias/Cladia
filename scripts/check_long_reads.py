"""Real Linux validation with a private library and deterministic synthetic reads."""
import json
import os
import sys
import tempfile
import time
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
_scratch = tempfile.TemporaryDirectory(prefix='cladia-long-')
scratch = Path(_scratch.name)
os.environ['CLADIA_DATA'] = str(scratch / 'data')
(scratch / 'data/library').mkdir(parents=True)
(scratch / 'data/.cladia').mkdir(parents=True)
from Bio import SeqIO
from Bio.Align import PairwiseAligner
from cladia import config, jobs, library, mapping
import cladia.execution as owner_execution
import cladia.tool_plugins.minimap2 as owner_minimap2
from make_long_reads import simulate

# Exercise origin padding and read splitting, not just a linear reference.
record = SeqIO.read(ROOT / 'tests/data/synthetic_mt.gb', 'genbank')
record.annotations['topology'] = 'circular'
circle = scratch / 'circle.gb'; SeqIO.write(record, circle, 'genbank')
ids, errors = library.import_paths([circle])
assert not errors, errors
reference = library.get_item(ids[0])
truth = str(SeqIO.read(ROOT / 'tests/data/synthetic_mt.gb', 'genbank').seq).upper()
owner_execution.ensure_tools_image({'log': ''})
work_version = scratch / 'version'; work_version.mkdir()
code, version, err, _ = owner_execution.run_container(config.TOOLS_IMAGE, ['minimap2', '--version'], work_version, 'cladia-qa-version')
assert code == 0 and version.strip() == '2.31-r1302', (version, err)
results = [{'minimap2': version.strip()}]
for kind, error, fasta in [('ont', .08, False), ('ont_hq', .005, False), ('clr', .08, False), ('hifi', .005, False), ('hifi', .005, True)]:
    name = kind + ('-fasta' if fasta else '')
    reads = scratch / (name + ('.fa' if fasta else '.fq'))
    simulate(truth, reads, error=error, fasta=fasta)
    work = scratch / name; work.mkdir()
    job = {'id': 'qa-' + name, 'cores': 4, 'log': '', 'outputs': []}
    start = time.perf_counter()
    owner_minimap2.run_mapping(job, work, reference=reference, groups=mapping.read_groups([reads], kind), output=name, read_type=kind)
    data = library.mapping_data(library.get_item(job['outputs'][0]['id']))
    consensus, _ = mapping.consensus_sequence(data['consensus_ref'], {int(k): v for k, v in data['insertions'].items()})
    aligner = PairwiseAligner(mode='global', match_score=1, mismatch_score=-1, open_gap_score=-2, extend_gap_score=-1)
    aln = aligner.align(truth, consensus)[0]
    counts = aln.counts()
    identity = counts.identities / (counts.identities + counts.mismatches + counts.gaps)
    entry = dict(kind=name, seconds=round(time.perf_counter()-start, 2), identity=identity,
                 mean_depth=data['summary']['mean_depth'], covered=data['summary']['covered_percent'],
                 mapped=data['summary']['mapped_percent'])
    entry['mean_agreement'] = sum(data['agreement']) / len(data['agreement'])
    entry['peak_rss_gb'] = re.findall(r'Peak RSS: ([\d.]+) GB', (work / 'run.log').read_text())
    results.append(entry)
    assert entry['covered'] > 99, entry
    assert identity > (.995 if kind in {'hifi', 'ont_hq'} else .95), entry

# Short paired reads still use sr and file-name pairing after the tools upgrade.
from Bio.Seq import Seq
paths = [scratch / 'short_R1.fq', scratch / 'short_R2.fq']
with paths[0].open('w') as left, paths[1].open('w') as right:
    for n, start in enumerate(range(0, len(truth), 25)):
        fragment = (truth * 2)[start:start + 400]
        for mate, handle, bases in [(1, left, fragment[:150]), (2, right, str(Seq(fragment[-150:]).reverse_complement()))]:
            handle.write(f'@pair{n}/{mate}\n{bases}\n+\n' + 'I' * len(bases) + '\n')
work = scratch / 'illumina'; work.mkdir()
job = {'id': 'qa-illumina', 'cores': 4, 'log': '', 'outputs': []}
groups = mapping.read_groups(paths)
assert len(groups) == 1 and len(groups[0]['files']) == 2
owner_minimap2.run_mapping(job, work, reference=reference, groups=groups, output='illumina')
data = library.mapping_data(library.get_item(job['outputs'][0]['id']))
assert data['summary']['covered_percent'] == 100
assert data['consensus_ref'] == truth
results.append({'illumina': data['summary']['covered_percent'], 'preset': data['options']['preset']})

# HiFi extension: a reference missing 300 bp at both ends, reads from the truth.
record = SeqIO.read(ROOT / 'tests/data/synthetic_mt.gb', 'genbank')[300:-300]
record.annotations['topology'] = 'linear'
piece = scratch / 'piece.gb'; SeqIO.write(record, piece, 'genbank')
ids, errors = library.import_paths([piece]); assert not errors
work = scratch / 'extension'; work.mkdir()
job = {'id': 'qa-extension', 'cores': 4, 'log': '', 'outputs': []}
reads = scratch / 'hifi.fq'
owner_minimap2.run_mapping(job, work, reference=library.get_item(ids[0]), groups=mapping.read_groups([reads], 'hifi'),
             output='extension', read_type='hifi', extend_rounds=1)
data = library.mapping_data(library.get_item(job['outputs'][0]['id']))
assert data['extension']['left'] > 0 and data['extension']['right'] > 0, data['extension']
results.append({'hifi_extension': data['extension']})
print(json.dumps({'scratch': str(scratch), 'results': results}, indent=2))
artifacts = ROOT / 'build/long-read-results.json'
artifacts.parent.mkdir(parents=True, exist_ok=True)
artifacts.write_text(json.dumps({'scratch': str(scratch), 'results': results}, indent=2))
