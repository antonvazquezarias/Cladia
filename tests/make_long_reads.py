"""Deterministic long-read simulator for mapping QA (no extra dependencies).

python tests/make_long_reads.py reference.gb output.fastq --error .08 --depth 50
Circular sampling, both strands, substitutions and indels; errors are synthetic,
not a calibrated model of a sequencing platform.
"""
import argparse
import random
import math
from pathlib import Path
from Bio import SeqIO
from Bio.Seq import Seq


def simulate(sequence, output, error=.005, depth=50, seed=42, fasta=False):
    rng = random.Random(seed)
    length, total, n = len(sequence), 0, 0
    with open(output, 'w') as handle:
        while total < length * depth:
            size = rng.randint(min(3000, length), min(20000, length))
            start = rng.randrange(length)
            bases = (sequence * 2)[start:start + size]
            if rng.random() < .5:
                bases = str(Seq(bases).reverse_complement())
            read = []
            for base in bases:
                if rng.random() >= error:
                    read.append(base)
                else:
                    kind = rng.randrange(3)
                    if kind == 0: read.append(rng.choice([b for b in 'ACGT' if b != base]))
                    elif kind == 1: read.extend([rng.choice('ACGT'), base])
            text = ''.join(read)
            quality = max(2, min(40, round(-10 * math.log10(max(error, .0001)))))
            qualities = ''.join(chr(33 + max(2, min(40, quality + rng.randint(-2, 2)))) for _ in text)
            handle.write(f'>read{n}\n{text}\n' if fasta else f'@read{n}\n{text}\n+\n' + qualities + '\n')
            total += size; n += 1
    return n


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference'); parser.add_argument('output')
    parser.add_argument('--error', type=float, default=.005)
    parser.add_argument('--depth', type=int, default=50)
    parser.add_argument('--fasta', action='store_true')
    args = parser.parse_args()
    fmt = 'genbank' if Path(args.reference).suffix in {'.gb', '.gbk'} else 'fasta'
    sequence = str(SeqIO.read(args.reference, fmt).seq).upper()
    simulate(sequence, args.output, args.error, args.depth, fasta=args.fasta)
