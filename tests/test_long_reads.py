import pytest
from cladia import mapping, library


@pytest.mark.parametrize('kind,preset', [('ont', 'map-ont'), ('ont_hq', 'lr:hq'), ('hifi', 'map-hifi'), ('clr', 'map-pb')])
def test_long_read_presets_and_independent_files(kind, preset):
    paths = ['sample_R1.fastq.gz', 'sample_R2.fastq.gz']
    groups = mapping.read_groups(paths, kind)
    assert len(groups) == 2
    inside = dict(zip(paths, ['/reads/a.fq', '/reads/b.fq']))
    assert f'-ax {preset} ' in mapping.script(groups, inside, 5000, 3, 0, read_type=kind)
    assert f'-ax {preset} ' in mapping.ends_script(groups, inside, read_type=kind)
    assert '-F 0xF04' in mapping.script(groups, inside, 5000, 3, 0, read_type=kind)
    assert f'-B -Q {5 if kind in {"ont", "clr"} else 20}' in mapping.script(groups, inside, 5000, 3, 0, read_type=kind)


def test_gc_facts_ambiguity_and_cache_invalidation(tmp_path):
    path = tmp_path / 'sample.fasta'
    path.write_text('>a\nGGCCATNN\n>b\nNNNN\n')
    facts = library.record_meta(path)
    assert facts[0]['gc'] == pytest.approx(4 / 6)
    assert facts[1]['gc'] is None
    path.write_text('>a\nAAAA\n')
    assert library.record_meta(path)[0]['gc'] == 0


def test_long_read_split_preserves_indels_and_clipping():
    sequence = 'A' * 25
    pieces = mapping._split_read(90, '2S8M2I5M3D8M', sequence, 100)
    assert pieces == [(90, '2S8M2I2M', sequence[:14]), (0, '3M3D8M', sequence[14:])]
    assert sum(mapping._span(cigar) for _, cigar, _ in pieces) == 24
    assert ''.join(seq for _, _, seq in pieces) == sequence
