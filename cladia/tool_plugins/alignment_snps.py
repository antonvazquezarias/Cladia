"""Reference-based SNP and small-indel extraction from a nucleotide alignment
(built in: plain Python, nothing to download)."""
import hashlib
from pathlib import Path

from cladia import engine_api as api

ENGINE = {
    "id": "alignment_snps", "label": "Alignment variants", "order": 10,
    "blurb": "Find SNPs and small indels relative to a chosen alignment row and attach a VCF.",
    "supports": ["variants.alignment_snps"],
    "lane": "compute", "cores": 1, "jobKind": "alignment_snps", "storage": {"kind": "builtin"},
    "options": [{"key": "reference_row", "label": "Reference row", "type": "choice", "default": "",
                 "choices": [{"value": "", "label": "Choose a reference row"}],
                 "choicesFrom": {"kind": "alignment_rows", "slot": "alignment"}},
                {"key": "variant_types", "label": "Call", "type": "choice", "default": "snps_indels",
                 "choices": [{"value": "snps_indels", "label": "SNPs and indels"},
                             {"value": "snps", "label": "SNPs only"}]}],
}



def _repeat_unit(reference, anchor_position, changed):
    """Short motif repeated at the indel boundary, if the changed bases fit it."""
    for size in range(1, min(6, len(changed)) + 1):
        if len(changed) % size:
            continue
        motif = changed[:size]
        if motif * (len(changed) // size) != changed:
            continue
        run = motif * 3
        lo = max(0, anchor_position - 30)
        hi = min(len(reference) - len(run) + 1, anchor_position + 30)
        if any(reference[i:i + len(run)] == run and i <= anchor_position <= i + len(run)
               for i in range(lo, hi)):
            return motif
    return None


def _variant_rows(strings, names, reference_row, include_indels, job):
    """Return sorted VCF rows; flanking bases anchor alleles with an empty side."""
    ref_index = names.index(reference_row)
    ref = strings[ref_index]
    ungapped_ref = ref.replace("-", "").replace(".", "")
    length = len(ref)
    spans = []
    for sequence in strings:
        spans.append((len(sequence) - len(sequence.lstrip("-.")), len(sequence.rstrip("-.")) - 1))
    def internal_gap(column):
        return any(sequence[column] in "-." and first < column < last
                   for sequence, (first, last) in zip(strings, spans))
    positions = []
    count = 0
    for base in ref:
        if base not in "-.":
            count += 1
        positions.append(count)
    events, snps, indels = [], 0, 0
    absorbed_snps = set()
    column = 0
    while column < length:
        if column % 10000 == 0:
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            api.step(job, 0, column / max(1, length), f"Column {column + 1:,} of {length:,}")
        if include_indels and internal_gap(column):
            start = column
            while column < length and internal_gap(column):
                column += 1
            end = column
            segment = ref[start:end].replace("-", "").replace(".", "")
            if set(segment) - set("ACGT"):
                continue
            left = next((i for i in range(start - 1, -1, -1) if ref[i] not in "-."), None)
            right = next((i for i in range(end, length) if ref[i] not in "-."), None)
            candidates = [i for i in (left, right) if i is not None and ref[i] in "ACGT"]
            if not candidates:
                continue
            def covered(index, sequence):
                first, last = spans[index]
                return (index == ref_index or start >= first and end - 1 <= last) and not set(sequence[start:end]) - set("ACGT-.")
            # Prefer a flank shared by all callable samples. A SNP on the left
            # must not make an otherwise well-supported insertion disappear.
            anchor = next((i for i in candidates if all(sequence[i] == ref[i]
                          for index, sequence in enumerate(strings) if covered(index, sequence))), candidates[0])
            before = anchor == left
            allele = (ref[anchor] + segment) if before else (segment + ref[anchor])
            pos = positions[anchor] if before else positions[anchor] - len(segment)
            observed = []
            changed_block = False
            changed_anchor = False
            for index, sequence in enumerate(strings):
                if not covered(index, sequence) or sequence[anchor] not in "ACGT":
                    observed.append(None)
                else:
                    bases = sequence[start:end].replace("-", "").replace(".", "")
                    changed_block |= bases != segment
                    changed_anchor |= sequence[anchor] != ref[anchor]
                    observed.append((sequence[anchor] + bases) if before else (bases + sequence[anchor]))
            if not changed_block:
                continue  # all-gap padding is not an indel, even if a flank varies
            alts = list(dict.fromkeys(value for value in observed if value and value != allele))
            if alts:
                if changed_anchor:
                    # Both flanks can vary: keep the anchor SNP inside this
                    # complex allele rather than reporting the same change twice.
                    absorbed_snps.add(anchor)
                    previous = len(events)
                    events = [event for event in events if not (event[1] == 1 and event[0] == positions[anchor])]
                    snps -= previous - len(events)
                repeat = None
                for value in observed:
                    if not value or value == allele:
                        continue
                    raw = value[1:] if before else value[:-1]
                    if bool(segment) == bool(raw):
                        continue  # complex replacement, not a simple repeat-length change
                    repeat = _repeat_unit(ungapped_ref, pos if before else pos - 1, segment or raw)
                    if repeat:
                        break
                info = f"TYPE=INDEL;RU={repeat}" if repeat else "TYPE=INDEL"
                calls = [str(([allele, *alts].index(value))) if value in [allele, *alts] else "."
                         for value in observed]
                events.append((pos, 0, f"{reference_row}\t{pos}\t.\t{allele}\t{','.join(alts)}\t.\tPASS\t{info}\tGT\t" + "\t".join(calls)))
                indels += 1
            continue
        base = ref[column]
        if base in "ACGT" and column not in absorbed_snps:
            observed = [sequence[column] if sequence[column] in "ACGT" else None for sequence in strings]
            alts = list(dict.fromkeys(value for value in observed if value and value != base))
            if alts:
                pos = positions[column]
                calls = [str(([base, *alts].index(value))) if value in [base, *alts] else "."
                         for value in observed]
                events.append((pos, 1, f"{reference_row}\t{pos}\t.\t{base}\t{','.join(alts)}\t.\tPASS\t.\tGT\t" + "\t".join(calls)))
                snps += 1
        column += 1
    events.sort(key=lambda event: (event[0], event[1]))
    return [event[2] for event in events], snps, indels


def run(job, workdir, operation, items, output, reference_row="", variant_types="snps_indels", save_copy=False):
    if operation != "variants.alignment_snps" or len(items) != 1 or items[0]["kind"] != "alignment":
        raise ValueError("Choose one nucleotide alignment")
    item = items[0]
    source_hash = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
    records = api.records(item)
    names = [record.id for record in records]
    if len(records) < 2 or len(set(names)) != len(names) or reference_row not in names:
        raise ValueError("Choose a unique reference row in an alignment with at least two sequences")
    if variant_types not in ("snps", "snps_indels"):
        raise ValueError("Choose SNPs only or SNPs and indels")
    strings = [str(record.seq).upper() for record in records]
    if len({len(sequence) for sequence in strings}) != 1 or any(
            set(sequence) - set("ACGTRYSWKMBDHVN-.") for sequence in strings):
        raise ValueError("The alignment must contain nucleotide sequences of equal length")
    if any(not name or any(char.isspace() for char in name) for name in names):
        raise ValueError("Alignment row names cannot contain whitespace in VCF")
    ref = strings[names.index(reference_row)]
    vcf = ["##fileformat=VCFv4.3", "##source=CladiaAlignmentVariants",
           f"##contig=<ID={reference_row},length={len(ref.replace('-', '').replace('.', ''))}>",
           '##INFO=<ID=TYPE,Number=1,Type=String,Description="Cladia call class for complex gap blocks">',
           '##INFO=<ID=RU,Number=1,Type=String,Description="Short repeat motif at a simple indel boundary">',
           '##FORMAT=<ID=GT,Number=1,Type=String,Description="Haploid allele index; missing for ambiguity">',
           "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(names)]
    api.steps(job, "Scanning alignment", "Saving VCF")
    rows, snps, indels = _variant_rows(strings, names, reference_row, variant_types == "snps_indels", job)
    vcf.extend(rows)
    api.step(job, 1, detail=f"{snps} SNP(s), {indels} indel(s)")
    api.attach_alignment_variants(job, item, "\n".join(vcf) + "\n", reference_row, source_hash, save_copy)
    job["log"] += (f"Found {snps} SNP and {indels} indel sites. Terminal gaps and ambiguous bases "
                   "are missing calls; complex gap blocks are represented as one allele.\n")
