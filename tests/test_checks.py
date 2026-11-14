from cladia import checks


def feat(**kw):
    base = {"id": "f0", "name": "x", "type": "CDS", "strand": 1, "notes": {"product": ["p"]}}
    base.update(kw)
    return base


def codes(data):
    return {p["code"] for p in checks.check(data)}


def test_good_cds_has_only_the_gene_warning():
    data = {"sequence": "ATGAAATAA", "organism": "X y", "features": [feat(start=0, end=8)]}
    assert codes(data) == {"no-gene"}


def test_cds_problems():
    data = {"sequence": "AAAAAATAAGG", "organism": "X", "features": [feat(start=0, end=9)]}
    assert {"cds-frame", "cds-start"} <= codes(data)
    data = {"sequence": "ATGTAAATGAAATAA", "organism": "X", "features": [feat(start=0, end=14, notes={})]}
    assert {"cds-internal-stop", "product"} <= codes(data)


def test_minus_strand_and_code():
    data = {"sequence": "TTATTTCAT", "organism": "X", "features": [feat(start=0, end=8, strand=-1)]}
    assert not codes(data) & {"cds-stop", "cds-start"}
    # TGA is a stop in table 1 but Trp in table 4
    data = {"sequence": "ATGTGATAA", "organism": "X", "geneticCode": 4, "features": [feat(start=0, end=8)]}
    assert "cds-internal-stop" not in codes(data)
    data["geneticCode"] = 1
    assert "cds-internal-stop" in codes(data)


def test_sequence_rnas_genes_notes():
    data = {"sequence": "ACGTZ", "features": [
        feat(type="tRNA", start=0, end=2, notes={"product": ["Met"]}),
        feat(type="gene", start=3, end=4, notes={}),
        feat(type="misc_feature", start=0, end=9, notes={}),
        feat(type="rRNA", start=0, end=1, notes={"product": ["rnl"], "note": ["copied from X, 90 % identical"]})]}
    assert {"bases", "organism", "trna-name", "gene-name", "gene-alone", "outside", "cladia-note"} <= codes(data)
