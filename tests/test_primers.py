"""Primer3 output parsing and the small in-silico PCR."""

from cladia import primers


def test_iupac_reverse_complement():
    assert primers.reverse_complement("ACGTRYN") == "NRYACGT"
    assert primers.normalize("ac gu") == "ACGT"


def test_find_sites_linear_and_mismatch():
    sites = primers.find_sites("AACCGGTTAACC", "AACC", 0, False)
    assert [s["start"] for s in sites] == [0, 8]
    assert sites[0]["end"] == 3 and sites[1]["end"] == 11
    one = primers.find_sites("AACCGGTTAACC", "AAGC", 1, False)
    assert [s["start"] for s in one] == [0, 8]
    assert one[0]["mismatches"] == 1
    assert primers.find_sites("AACCGGTTAACC", "AAGC", 0, False) == []


def test_test_pair_products():
    seq = "TTTT" + "AACCGGTT" + "GGGG" + "CCAATTGG" + "TTTT"
    hits = primers.test_pair(seq, "AACCGGTT", "CCAATTGG", 0, 8, 30, False, False)
    assert any(h["product_size"] == 20 for h in hits)
    assert all(h["strand"] == 1 for h in hits)
    assert all(h["forward"]["start"] == 5 for h in hits)


def test_parse_design():
    text = """PRIMER_LEFT_0_SEQUENCE=AAAACCCC
PRIMER_RIGHT_0_SEQUENCE=TTTTGGGG
PRIMER_LEFT_0_POSITION=10,8
PRIMER_RIGHT_0_POSITION=107,8
PRIMER_LEFT_0_TM=60.1
PRIMER_RIGHT_0_TM=61.2
PRIMER_LEFT_0_GC_PERCENT=50
PRIMER_RIGHT_0_GC_PERCENT=50
PRIMER_PAIR_0_PRODUCT_SIZE=98
PRIMER_PAIR_0_PENALTY=1.25
"""
    pairs, warnings = primers.parse_design(text)
    assert not warnings
    assert pairs[0]["left"]["sequence"] == "AAAACCCC"
    assert pairs[0]["left"]["start"] == 10
    assert pairs[0]["right"]["start"] == 100
    assert pairs[0]["product_size"] == 98
    assert pairs[0]["penalty"] == 1.25


def test_map_design():
    pair = {"left": {"sequence": "AAA", "start": 10, "length": 3, "tm": 60, "gc": 50, "penalty": 0.25},
            "right": {"sequence": "TTT", "start": 30, "length": 3, "tm": 60, "gc": 50, "penalty": 0.5},
            "product_size": 23, "penalty": 1.25}
    linear = primers.map_design([pair], 0, 100)
    assert linear[0]["left"]["start"] == 11 and linear[0]["product_size"] == 23
    assert linear[0]["penalty"] == 1.25  # the pair's, not a primer's
    circular = primers.map_design([pair], 95, 100, circular=True)
    assert circular[0]["left"]["start"] == 6
    assert circular[0]["right"]["start"] == 26
    assert circular[0]["penalty"] == 1.25


def test_circular_template():
    template, rotation, target, length = primers.circular_design_template("ACGTACGT", 4, 1)
    assert rotation == 3 and target == 1 and length == 6
    assert template[target:target + length] == "ACGTAC"


def test_three_prime_mismatches_of_the_reverse_primer():
    """The reverse primer's 3' end is the start of its reverse complement on the plus strand."""
    import random
    random.seed(5)
    seq = "".join(random.choice("ACGT") for _ in range(600))
    forward, reverse = seq[50:70], primers.reverse_complement(seq[400:420])
    swap = {"A": "C", "C": "G", "G": "T", "T": "A"}
    at_3 = reverse[:-1] + swap[reverse[-1]]
    at_5 = swap[reverse[0]] + reverse[1:]
    (hit,) = primers.test_pair(seq, forward, at_3, mismatches=1, both_orientations=False)
    assert (hit["reverse"]["mismatches"], hit["reverse"]["three"]) == (1, 1)
    (hit,) = primers.test_pair(seq, forward, at_5, mismatches=1, both_orientations=False)
    assert (hit["reverse"]["mismatches"], hit["reverse"]["three"]) == (1, 0)
    # the forward primer: its 3' end is its own end
    (hit,) = primers.test_pair(seq, forward[:-1] + swap[forward[-1]], reverse, mismatches=1, both_orientations=False)
    assert hit["forward"]["three"] == 1


def test_three_prime_rule():
    """A limit on mismatches in the primers' last five bases: a 3'-end mismatch drops the
    product, the same number of mismatches at the 5' end doesn't."""
    import random
    random.seed(5)
    seq = "".join(random.choice("ACGT") for _ in range(600))
    forward, reverse = seq[50:70], primers.reverse_complement(seq[400:420])
    swap = {"A": "C", "C": "G", "G": "T", "T": "A"}
    at_3 = reverse[:-1] + swap[reverse[-1]]
    at_5 = swap[reverse[0]] + reverse[1:]
    run = lambda r, three: primers.test_pair(seq, forward, r, mismatches=1, both_orientations=False, three_prime=three)
    assert run(at_3, 0) == [] and len(run(at_3, 1)) == 1 and len(run(at_3, None)) == 1
    assert len(run(at_5, 0)) == 1
