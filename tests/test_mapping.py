import cladia.execution as owner_execution
import cladia.tool_plugins.minimap2 as owner_minimap2
"""Mapping reads: pairing the files, reading samtools' output, the consensus and
its features, and the job with the container replaced by a stand-in.

Run with:  python -m pytest tests
"""

import shutil
from pathlib import Path

import pytest
from Bio import SeqIO
from fastapi.testclient import TestClient

from cladia import config, jobs, library, mapping

DATA = Path(__file__).parent / "data"


# ── Pairing ──────────────────────────────────────────────────────────────

def test_pairing_by_name():
    files = ["/r/S1_L001_R1_001.fastq.gz", "/r/S1_L001_R2_001.fastq.gz",
             "/r/S1_L002_R1_001.fastq.gz", "/r/S1_L002_R2_001.fastq.gz",
             "/r/other_1.fq.gz", "/r/other_2.fq.gz", "/r/lonely_R1.fastq"]
    groups = mapping.pair_reads(files)
    assert [(g["name"], [Path(f).name for f in g["files"]]) for g in groups] == [
        ("S1_L001", ["S1_L001_R1_001.fastq.gz", "S1_L001_R2_001.fastq.gz"]),
        ("S1_L002", ["S1_L002_R1_001.fastq.gz", "S1_L002_R2_001.fastq.gz"]),
        ("other", ["other_1.fq.gz", "other_2.fq.gz"]),
        ("lonely_R1", ["lonely_R1.fastq"]),  # no R2: single-end
    ]
    # R2 picked before R1: still in the right order
    assert [Path(f).name for f in mapping.pair_reads(["/r/a_R2.fq", "/r/a_R1.fq"])[0]["files"]] == ["a_R1.fq", "a_R2.fq"]


def test_script_mounts_each_folder_once():
    groups = mapping.pair_reads(["C:/runs/a/x_R1.fq.gz", "C:/runs/a/x_R2.fq.gz", "C:/runs/b/y.fq.gz"])
    folders, inside = mapping.mounts_for(groups)
    assert sorted(folders.values()) == ["/reads/0", "/reads/1"]
    text = mapping.script(groups, inside, 15789, min_depth=3, min_mapq=0)
    assert "minimap2 -t $T -ax sr /data/ref.fasta '/reads/0/x_R1.fq.gz' '/reads/0/x_R2.fq.gz'" in text
    assert "samtools merge" in text and "-r ref:1-15789" in text and "--show-ins no" in text
    assert "-F 0xF04" in text and "reads_raw.tsv" in text  # the reads lane's data
    # the job's cores (Settings), no more than the Linux side has
    assert "T=$(( 1 < $(nproc) ? 1 : $(nproc) ))" in text
    assert "T=$(( 6 < $(nproc) ? 6 : $(nproc) ))" in mapping.script(groups, inside, 15789, 3, 0, cores=6)
    assert "T=$(( 6 < $(nproc)" in mapping.ends_script(groups, inside, cores=6)


# ── samtools output ──────────────────────────────────────────────────────

def test_depth_folds_the_extension(tmp_path):
    # a 10 bp circular reference mapped with 3 bases appended (positions 11-13)
    (tmp_path / "depth.txt").write_text("".join(f"ref\t{p}\t{d}\n" for p, d in
                                               enumerate([1, 1, 1, 5, 5, 5, 5, 5, 5, 5, 4, 4, 4], start=1)))
    depth, raw = mapping.read_depth(tmp_path / "depth.txt", 10)
    assert depth == [5] * 10 and len(raw) == 13
    # the first positions' consensus from the copy with more reads (here the appended one)
    merged, ins = mapping.merge_origin("NNNACGTACG", {1: "T"}, "ACG", {}, raw, 10)
    assert merged == "ACGACGTACG" and ins == {}


def test_consensus_with_insertions(tmp_path):
    # reference ACGTACGTAC; the reads: T->G at 4, G deleted at 7, AA inserted after 9
    (tmp_path / "ref.fa").write_text(">ref\nACGGAC*TAC\n")
    (tmp_path / "ins.fa").write_text(">ref\nACGGAC*TAAAC\n")
    per_position, insertions = mapping.read_consensus(tmp_path / "ref.fa", tmp_path / "ins.fa", 10)
    assert per_position == "ACGGAC*TAC" and insertions == {8: "AA"}
    seq, where = mapping.consensus_sequence(per_position, insertions)
    assert seq == "ACGGACTAAAC"
    assert where == [0, 1, 2, 3, 4, 5, 6, 6, 7, 10]
    s = mapping.summary("ACGTACGTAC", [9] * 10, per_position, insertions, {}, 3, [])
    assert [(d["kind"], d["start"]) for d in s["differences"]] == [("substitution", 3), ("deletion", 6), ("insertion", 8)]


def test_features_follow_the_consensus():
    per_position, insertions = "ACGGAC*TAC", {8: "AA"}
    seq, where = mapping.consensus_sequence(per_position, insertions)
    features = [{"id": "f0", "name": "a", "type": "gene", "start": 4, "end": 9, "strand": 1, "notes": {}},
                {"id": "f1", "name": "gone", "type": "misc_feature", "start": 6, "end": 6, "strand": 1, "notes": {}},
                {"id": "f2", "name": "split", "type": "CDS", "start": 0, "end": 9, "strand": 1, "notes": {},
                 "locations": [{"start": 0, "end": 2}, {"start": 7, "end": 9}]}]
    lifted = {f["name"]: f for f in mapping.lift_features(features, per_position, where, 10)}
    assert (lifted["a"]["start"], lifted["a"]["end"]) == (4, 10)  # longer by the insertion
    assert seq[4:11] == "ACTAAAC"
    assert "gone" not in lifted  # all of it deleted
    assert lifted["split"]["locations"] == [{"start": 0, "end": 2}, {"start": 6, "end": 10}]
    assert "id" not in lifted["a"]


# ── The job ──────────────────────────────────────────────────────────────

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "Cladia" / ".cladia")
    library.ensure_folders()
    monkeypatch.setattr(owner_execution, "ensure_tools_image", lambda job: None)
    reads = tmp_path / "reads"
    reads.mkdir()
    for name in ("s_R1.fastq.gz", "s_R2.fastq.gz"):
        (reads / name).write_bytes(b"not really gzip")
    record = SeqIO.read(DATA / "synthetic_mt.gb", "genbank")
    record.annotations["topology"] = "circular"
    SeqIO.write(record, tmp_path / "circular_mt.gb", "genbank")
    ids, _ = library.import_paths([tmp_path / "circular_mt.gb"])
    return library.get_item(ids[0]), reads


def test_map_job_makes_a_mapping_item(setup, tmp_path, monkeypatch):
    reference, reads = setup
    seq = str(library.records(reference)[0].seq)
    length = len(seq)
    calls = []

    def fake_container(job, image, args, workdir, mounts=None):
        calls.append((args, mounts, (workdir / "map.sh").read_text()))
        mutated = seq[:99] + ("A" if seq[99] != "A" else "C") + seq[100:]  # one substitution at 100
        (workdir / "consensus_ref.fa").write_text(">ref\n" + mutated + "\n")
        ins = "T" if mutated[200] != "T" else "G"  # inserted after position 200 (0-based 199)
        (workdir / "consensus_ins.fa").write_text(">ref\n" + mutated[:200] + ins + mutated[200:] + "\n")
        (workdir / "consensus_ext_ref.fa").write_text(">ref\n" + "N" * 1000 + "\n")
        (workdir / "consensus_ext_ins.fa").write_text(">ref\n" + "N" * 1000 + "\n")
        (workdir / "depth.txt").write_text("".join(f"ref\t{p}\t{0 if p <= 50 else 20}\n" for p in range(1, length + 1001)))
        (workdir / "stats.txt").write_text("SN\traw total sequences:\t1000\nSN\treads mapped:\t250\nSN\tinsert size average:\t310.5\n")
        # base counts: 20 reads agree everywhere but at 300 (60 A, 40 G); the appended start folds back
        (workdir / "counts.txt").write_text("".join(
            f"{p} {'60 0 40 0 0' if p == 300 else '20 0 0 0 0'}\n" for p in range(1, length + 1001)))
        (workdir / "clips.txt").write_text("L 500 7\nR 500 3\nR 1200 2\n")
        (workdir / "reads_raw.tsv").write_text(
            "0\t100\t60\t20M\t" + "A" * 20 + "\n" + "16\t" + str(length - 15) + "\t60\t40M\t" + "C" * 40 + "\n")
        (workdir / "mapped.bam").write_bytes(b"BAM")
        (workdir / "mapped.bam.bai").write_bytes(b"BAI")
        (workdir / "run.log").write_text("[M::main] Real time: 1.0 sec\n")
        return ""

    monkeypatch.setattr(owner_execution, "tool", fake_container)
    groups = mapping.pair_reads([reads / "s_R1.fastq.gz", reads / "s_R2.fastq.gz"])
    job = {"log": "", "outputs": [], "id": "m1"}
    (tmp_path / "w").mkdir()
    owner_minimap2.run_mapping(job, tmp_path / "w", reference, groups, "Mt reads", min_depth=3, save_bam=True)

    args, mounts, text = calls[0]
    assert args == ["bash", "/data/map.sh"]
    assert list(mounts.values()) == ["/reads/0"] and Path(list(mounts)[0]) == reads.resolve()
    ref_fasta = (tmp_path / "w" / "ref.fasta").read_text().splitlines()[1]
    assert len(ref_fasta) == length + 1000  # circular: the start appended

    item = library.get_item(job["outputs"][0]["id"])
    assert item["kind"] == "mapping" and item["label"] == "Mt reads" and item["bam"] == "Mt_reads.bam"
    data = library.mapping_data(item)
    s = data["summary"]
    assert s["mapped_percent"] == 25.0 and s["insert_size"] == 310.5
    assert data["depth"][0] == 20  # folded: the extension's 20 added to the start's 0
    assert [(d["kind"], d["start"]) for d in s["differences"]] == [("substitution", 99), ("insertion", 199)]
    assert "Real time" in Path(job["live_log"]).read_text()  # saved with the log by jobs._run
    # 300: 60 A + 40 G, and the appended copy's 20 A folded onto it: 80 of 120
    assert data["agreement"][299] == 66 and data["agreement"][10] == 100
    assert [v["pos"] for v in data["variable"]] == [299] and data["variable"][0]["counts"]["G"] == 40
    assert data["clips"] == [[499, 10], [1199, 2]] and s["variable_count"] == 1
    assert data["extension"] == {"left": 0, "right": 0, "rounds": 0}
    # the reads lane's sidecar: the read across the circular origin is two pieces
    assert item["reads"] == "Mt_reads.reads.tsv" and data["reads"]["kept"] == 3
    from cladia.server import app
    window = TestClient(app).get(f"/api/mapping/{item['id']}/reads?start=0&end=20").json()
    assert [r[0] for r in window["reads"]] == [0]

    # the consensus as a sequence, with the features moved by the insertion
    client = TestClient(app)
    new_id = client.post(f"/api/mapping/{item['id']}/consensus").json()["id"]
    record = SeqIO.read(library.get_item(new_id)["path"], "genbank")
    original = library.records(reference)[0]
    assert len(record) == length + 1 and record.annotations.get("organism") == original.annotations.get("organism")
    cob, cob_new = (next(f for f in r.features if f.type == "CDS") for r in (original, record))
    assert int(cob_new.location.start) == int(cob.location.start) + 1  # after the insertion at 200

    # Export: consensus as FASTA, BAM copied with its index; Remove deletes the BAM too
    fasta = client.post("/api/export", json={"ids": [item["id"]], "format": "fasta"}).json()["text"]
    assert fasta.startswith(">Mt_reads_consensus")
    out = tmp_path / "out.bam"
    assert client.post("/api/export", json={"ids": [item["id"]], "format": "bam", "path": str(out)}).status_code == 200
    assert out.read_bytes() == b"BAM" and Path(f"{out}.bai").exists()
    client.post("/api/library/remove", json={"ids": [item["id"]]})
    assert not (library.library_dir() / "Mt_reads.bam").exists()
    assert not (library.library_dir() / "Mt_reads.reads.tsv").exists()


def test_pairs_endpoint(setup):
    _, reads = setup
    from cladia.server import app
    client = TestClient(app)
    groups = client.post("/api/reads/pairs", json={"paths": [str(reads / "s_R2.fastq.gz"), str(reads / "s_R1.fastq.gz")]}).json()
    assert len(groups) == 1 and groups[0]["name"] == "s" and groups[0]["size"] == 30
    assert client.post("/api/reads/pairs", json={"paths": ["C:/nowhere/x.fq"]}).status_code == 400


def test_long_read_api_validation_and_groups(setup, monkeypatch):
    from cladia.server import app
    reference, reads = setup
    client = TestClient(app)
    paths = [str(reads / 's_R1.fastq.gz'), str(reads / 's_R2.fastq.gz')]
    groups = client.post('/api/reads/pairs', json={'paths': paths, 'read_type': 'ont'}).json()
    assert len(groups) == 2 and all(len(g['files']) == 1 for g in groups)
    captured = []
    monkeypatch.setattr(jobs, 'submit', lambda *a, **k: captured.append(k) or {'ok': True})
    request = {'operation': 'map.reads', 'engine': 'minimap2',
               'inputs': {'reference': [reference['id']], 'read_files': paths},
               'options': {'read_type': 'ont'}}
    assert client.post('/api/tool-registry/run', json=request).status_code == 200
    assert captured[-1]['read_type'] == 'ont' and len(captured[-1]['groups']) == 2
    assert client.post('/api/tool-registry/run', json={**request,
        'options': {'read_type': 'ont', 'extend': True, 'extend_rounds': 1}}).status_code == 400
    assert client.post('/api/tool-registry/run', json={**request,
        'options': {'read_type': 'hifi', 'extend': True, 'extend_rounds': 1}}).status_code == 200
    assert client.post('/api/tool-registry/run', json={**request,
        'options': {'read_type': 'bad; command'}}).status_code == 400


# ── Extending the ends of a linear reference ─────────────────────────────

def _sam_alignment(contig, pos, cigar, seq):
    return f"r\t0\t{contig}\t{pos}\t60\t{cigar}\t*\t0\t0\t{seq}\t*\n"


def test_extend_ends_from_clipped_reads(tmp_path):
    # a 100 bp "whole" reference; the true sequence goes on with GGGCCC... after it
    # and with TTTAAA... before it
    lines = [_sam_alignment("whole", 1, "6S20M", "TTTAAA" + "C" * 20)] * 3      # 6 bases before the start
    lines += [_sam_alignment("whole", 1, "3S20M", "AAA" + "C" * 20)] * 2          # 3 of them, agreeing
    lines += [_sam_alignment("whole", 81, "20M4S", "G" * 20 + "GGGC")] * 3         # 4 bases after the end
    lines += [_sam_alignment("whole", 81, "20M2S", "G" * 20 + "TT")]               # 1 of 4 disagrees at +1
    lines += [_sam_alignment("whole", 41, "20M5S", "A" * 25)]                      # clipped inside: not an overhang
    (tmp_path / "ends.sam").write_text("".join(lines))
    reads = mapping.clipped_reads(tmp_path / "ends.sam")
    assert reads[0] == ("whole", 0, 19, "TTTAAA", "")
    left, right = mapping.extend_ends(reads, {"whole": 100}, min_depth=3, min_agree=0.7)
    # the first 3 bases before the start have 5 reads (all A), the next 3 only 3 (T): kept at depth 3
    assert left == "TTTAAA"
    # after the end: +1 G in 3 of 4 (75 %), +2 G 3, +3 G 3, +4 C 3
    assert right == "GGGC"
    left, right = mapping.extend_ends(reads, {"whole": 100}, min_depth=4, min_agree=0.7)
    assert (left, right) == ("AAA", "GG")  # depth 4: only where 4+ reads reach


def test_map_job_extends_a_linear_reference(setup, tmp_path, monkeypatch):
    from cladia.genbank import write_record
    reference, reads = setup
    record = library.records(reference)[0][:3000]  # a linear piece of it, with its features
    record.annotations["topology"] = "linear"
    write_record(record, tmp_path / "piece.gb")
    (pid,), _ = library.import_paths([tmp_path / "piece.gb"])
    piece = library.get_item(pid)
    seq = str(record.seq)
    rounds = []

    def fake_container(job, image, args, workdir, mounts=None):
        if args[1] == "/data/unpack.sh":  # the reads unpacked once into the job's volume
            assert mounts["volume:cladia-m2"] == "/vol" and "> /vol/1.fq" in (workdir / "unpack.sh").read_text()
            return ""
        if args[1] == "/data/ends.sh":  # two rounds grow 10 + 5 bp at the end, then nothing
            assert "'/vol/0.fq' '/vol/1.fq'" in (workdir / "ends.sh").read_text() and "volume:cladia-m2" in mounts
            names = [line[1:].strip() for line in (workdir / "ends.fasta").read_text().splitlines() if line.startswith(">")]
            rounds.append(names)
            grow = {1: "ACGTACGTAC", 2: "GGGTT"}.get(len(rounds), "")
            right_len = len((workdir / "ends.fasta").read_text().splitlines()[-1])
            lines = [_sam_alignment(names[-1], right_len - 19, f"20M{len(grow)}S", "A" * 20 + grow)] * 3 if grow else []
            (workdir / "ends.sam").write_text("".join(lines))
            return ""
        length = len((workdir / "ref.fasta").read_text().splitlines()[1])
        (workdir / "consensus_ref.fa").write_text(">ref\n" + (workdir / "ref.fasta").read_text().splitlines()[1] + "\n")
        (workdir / "consensus_ins.fa").write_text((workdir / "consensus_ref.fa").read_text())
        (workdir / "depth.txt").write_text("".join(f"ref\t{p}\t30\n" for p in range(1, length + 1)))
        (workdir / "counts.txt").write_text("".join(f"{p} 30 0 0 0 0\n" for p in range(1, length + 1)))
        (workdir / "clips.txt").write_text("")
        (workdir / "reads_raw.tsv").write_text("0\t1\t60\t20M\t" + "A" * 20 + "\n")
        (workdir / "stats.txt").write_text("SN\traw total sequences:\t100\nSN\treads mapped:\t90\n")
        return ""

    monkeypatch.setattr(owner_execution, "tool", fake_container)
    volumes = []  # wslc volume create / remove
    monkeypatch.setattr(owner_execution, "container_command", lambda: ["wslc"])
    monkeypatch.setattr(owner_execution, "_try", lambda cmd: (volumes.append(cmd[2:]), (True, ""))[1])
    groups = mapping.pair_reads([reads / "s_R1.fastq.gz", reads / "s_R2.fastq.gz"])
    job = {"log": "", "outputs": [], "id": "m2"}
    (tmp_path / "w2").mkdir()
    owner_minimap2.run_mapping(job, tmp_path / "w2", piece, groups, "extended", extend_rounds=5)
    assert rounds == [["whole"]] * 3  # short: mapped whole; the third round found nothing: stopped
    assert volumes == [["create", "cladia-m2"], ["remove", "cladia-m2"]]  # the reads' volume, gone at the end
    data = library.mapping_data(library.get_item(job["outputs"][0]["id"]))
    assert data["extension"] == {"left": 0, "right": 15, "rounds": 2}
    assert data["reference"]["sequence"] == seq + "ACGTACGTAC" + "GGGTT" and len(data["depth"]) == 3015
    ext = [f for f in data["reference"]["features"] if f["name"] == "extension (end)"]
    assert (ext[0]["start"], ext[0]["end"]) == (3000, 3014)
    assert "Extension round 3: 0 bp at the start, 0 bp at the end" in job["log"]
    assert data["reads"]["kept"] == 1 and job["outputs"][0]["kind"] == "mapping"


# ── The reads sidecar (the reads lane) ───────────────────────────────────

def _read_line(flag, pos, cigar, seq, mapq=60):
    return f"{flag}\t{pos}\t{mapq}\t{cigar}\t{seq}\n"


def test_reads_sidecar_linear(tmp_path):
    raw = tmp_path / "reads_raw.tsv"
    raw.write_text(_read_line(0, 101, "50M", "A" * 50) + _read_line(16, 201, "20S30M", "C" * 50)
                   + _read_line(0, 501, "10M5D35M", "G" * 45))
    reads = mapping.read_reads(raw, 1000, False)
    assert [(r[0], r[1], r[2]) for r in reads] == [(100, 149, "+"), (200, 229, "-"), (500, 549, "+")]
    info = mapping.write_reads(tmp_path / "reads.tsv", reads, 1000)
    assert info["kept"] == 3 and info["stride"] == 1 and info["max_span"] == 50
    window = mapping.read_window(tmp_path / "reads.tsv", info, 490, 560)
    assert [r[0] for r in window["reads"]] == [500] and window["in_view"] == 1
    assert mapping.read_window(tmp_path / "reads.tsv", info, 600, 700)["reads"] == []


def test_reads_window_sample_stays_stable_as_window_moves(tmp_path):
    reads = [(start, start + 9, "+", 60, "10M", "A" * 10) for start in range(0, 5000, 5)]
    info = mapping.write_reads(tmp_path / "reads.tsv", reads, 5000)
    left = mapping.read_window(tmp_path / "reads.tsv", info, 100, 300, limit=20, ref_length=5000)
    right = mapping.read_window(tmp_path / "reads.tsv", info, 150, 350, limit=20, ref_length=5000)
    # Reads wholly inside the overlap must be the same deterministic sample in
    # both responses, so a small pan cannot replace them with a different subset.
    overlap_left = {r[7] for r in left["reads"] if r[0] >= 160 and r[1] <= 290}
    overlap_right = {r[7] for r in right["reads"] if r[0] >= 160 and r[1] <= 290}
    assert overlap_left == overlap_right


def test_reads_window_all_mode_returns_every_stored_read(tmp_path):
    reads = [(start, start + 9, "+", 60, "10M", "A" * 10) for start in range(0, 1000, 5)]
    info = mapping.write_reads(tmp_path / "reads.tsv", reads, 1000)
    window = mapping.read_window(tmp_path / "reads.tsv", info, 100, 300,
                                 limit=mapping.READS_MAX, ref_length=1000, all_reads=True)
    assert window["all"] and not window["more"]
    assert [read[0] for read in window["reads"]] == list(range(95, 301, 5))


def test_reads_across_a_circular_origin(tmp_path):
    seq = "ACGT" * 15
    raw = tmp_path / "reads_raw.tsv"
    raw.write_text(_read_line(16, 50, "60M", seq))
    reads = mapping.read_reads(raw, 100, True)
    pieces = {r[0]: r for r in reads}
    assert (pieces[49][1], pieces[49][4], pieces[49][5]) == (99, "51M", seq[:51])
    assert (pieces[0][1], pieces[0][4], pieces[0][5]) == (8, "9M", seq[51:])
    assert pieces[0][2] == "-"


def test_reads_sidecar_samples_above_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(mapping, "READS_MAX", 3)
    raw = tmp_path / "reads_raw.tsv"
    raw.write_text("".join(_read_line(0, 10 + i * 20, "10M", "A" * 10) for i in range(10)))
    reads = mapping.read_reads(raw, 1000, False)
    info = mapping.write_reads(tmp_path / "reads.tsv", reads, 1000)
    assert info["total"] == 10 and info["kept"] == 3 and info["stride"] == 4
    assert info["bins"] and len([r for r in (tmp_path / "reads.tsv").read_text().splitlines() if r]) == 3
