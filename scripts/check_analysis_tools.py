"""Real analysis-job smoke checks in an isolated library."""
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_scratch = tempfile.TemporaryDirectory(prefix='cladia-tools-')
scratch = Path(_scratch.name)
os.environ['CLADIA_DATA'] = str(scratch / 'data')
(scratch / 'data/library').mkdir(parents=True)
(scratch / 'data/.cladia').mkdir(parents=True)
from Bio import Phylo
from cladia import config, jobs, library
import cladia.tool_plugins.fasttree as owner_fasttree
import cladia.execution as owner_execution
import cladia.tool_plugins.iqtree as owner_iqtree
import cladia.tool_plugins.mafft as owner_mafft
import cladia.tool_plugins.mauve as owner_mauve
import cladia.tool_plugins.primer3 as owner_primer3

owner_execution.ensure_tools_image({'log': ''})
def job(name):
    folder = scratch / name; folder.mkdir()
    return {'id': 'qa-' + name, 'log': '', 'outputs': [], 'cores': 2}, folder

def run(args, work):
    code, out, err, _ = owner_execution.run_container(config.TOOLS_IMAGE, args, work, 'cladia-qa-tools')
    assert code == 0, (args, err[-3000:])
    return out

rng = random.Random(137)
base = ''.join(rng.choices('ACGT', k=1500))
files = []
for i in range(6):
    seq = list(base)
    for p in rng.sample(range(len(base)), 150): seq[p] = rng.choice('ACGT')
    path = scratch / f's{i}.fa'; path.write_text(f'>s{i}\n' + ''.join(seq) + '\n'); files.append(path)
ids, errors = library.import_paths(files); assert not errors, errors
sequences = [library.get_item(i) for i in ids]
j, work = job('mafft')
owner_mafft.run_alignment(j, work, sequences[:5], 'aligned', 'auto', True)
alignment = library.get_item(j['outputs'][0]['id'])
assert len(library.alignment_rows(alignment)) == 5
j, work = job('mafft-add')
owner_mafft.run_alignment(j, work, sequences[5:], 'added', 'auto', False, add_to=alignment, keep_length=True)
alignment = library.get_item(j['outputs'][0]['id'])
assert len(library.alignment_rows(alignment)) == 6
for program in ['iqtree', 'fasttree']:
    j, work = job(program)
    (owner_fasttree.run_tree if program == "fasttree" else owner_iqtree.run_tree)(j, work, alignment, program, program, outgroup='s0')
    tree = library.get_item(j['outputs'][0]['id'])
    assert len(Phylo.read(tree['path'], 'newick').get_terminals()) == 6
    if program == 'iqtree':
        assert '3.1.4' in j['log'] and 'Best-fit model' in j['log']
        live = (work / 'run.log').read_text()
        assert owner_iqtree._watch_iqtree(live, j['progress'])['at'] == len(j['progress']['steps']) - 1

# Protein model detection and report parsing use the same IQ-TREE job path.
j, work = job('protein-tree')
protein_base = ''.join(rng.choices('ACDEFGHIKLMNPQRSTVWY', k=300))
rows = []
for i in range(6):
    aa = list(protein_base)
    for pos in rng.sample(range(300), 30): aa[pos] = rng.choice('ACDEFGHIKLMNPQRSTVWY')
    rows.append(f'>p{i}\n' + ''.join(aa) + '\n')
protein_path = work / 'protein.fa'; protein_path.write_text(''.join(rows))
protein_id = library.add_output(protein_path, 'protein-alignment', kind='alignment')
owner_iqtree.run_tree(j, work, library.get_item(protein_id), 'iqtree', 'protein-tree', bootstrap=False)
assert 'Best-fit model' in j['log']

# The retained genome-comparison workflow also invokes the upgraded BLAST.
j, work = job('mauve')
owner_mauve.run_comparison(j, work, sequences[:2], 'compared')
assert j['outputs']
j, work = job('primers')
owner_primer3.run_design(j, work, sequences[0])
assert json.loads(Path(library.get_item(j['outputs'][0]['id'])['path']).read_text())['pairs']

# Local nucleotide/protein DB creation and each genomic BLAST program Cladia offers.
j, work = job('blast')
codons = ['GCT', 'TGT', 'GAT', 'GAA', 'TTT', 'GGT', 'CAT', 'ATT', 'AAA', 'CTG',
          'ATG', 'AAT', 'CCT', 'CAA', 'CGT', 'TCT', 'ACT', 'GTT', 'TGG', 'TAT']
coding = ''.join(rng.choices(codons, k=300))
from Bio.Seq import Seq
(work / 'dna.fa').write_text('>target\n' + coding + '\n')
(work / 'aa.fa').write_text('>target\n' + str(Seq(coding).translate()) + '\n')
for name, kind in [('dna', 'nucl'), ('aa', 'prot')]:
    run(['makeblastdb', '-in', f'/data/{name}.fa', '-dbtype', kind, '-out', f'/data/{name}'], work)
for program, query, database in [('blastn', 'dna', 'dna'), ('blastp', 'aa', 'aa'),
                                 ('blastx', 'dna', 'aa'), ('tblastn', 'aa', 'dna'), ('tblastx', 'dna', 'dna')]:
    hits = run([program, '-query', f'/data/{query}.fa', '-db', f'/data/{database}', '-outfmt', '6'], work)
    assert hits.strip(), program

versions = {}
expected = {'mafft': 'v7.526', 'iqtree3': 'version 3.1.4', 'blastn': '2.17.0+',
            'samtools': 'samtools 1.25', 'minimap2': '2.31-r1302', 'fasttreeMP': 'FastTree 2.2.0',
            'primer3_core': 'release 2.6.1', 'progressiveMauve': 'build date', 'table2asn': '1.29.324'}
for tool, arg in [('mafft', '--version'), ('iqtree3', '--version'), ('blastn', '-version'),
                  ('samtools', '--version'), ('minimap2', '--version'), ('fasttreeMP', '-help'),
                  ('primer3_core', '--about'), ('progressiveMauve', '--version'), ('table2asn', '-version')]:
    code, out, err, _ = owner_execution.run_container(config.TOOLS_IMAGE, [tool, arg], work, 'cladia-qa-versions')
    text = (out + err).strip()
    assert code == 0 and expected[tool] in text, (tool, code, text)
    versions[tool] = text.splitlines()[0]
print(json.dumps({'scratch': str(scratch), 'versions': versions, 'passed': True}, indent=2))
