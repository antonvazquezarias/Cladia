"""Explicit host capabilities for engine modules (tool_plugins/).

run_container returns stdout and raises on failure. execute_process is the
lower-level provider returning (code, stdout, stderr, command).
"""
from . import execution as _execution, job_outputs as _outputs, job_progress as _progress
from . import config as _config, blastdb as _blastdb
from .genbank import file_format, read_records, write_fasta, write_record, editor_to_record, record_to_editor, genetic_code, looks_protein, rotated_feature, turned
from .blast_support import parse_blast, BLAST_PROGRAMS, LOCAL_DB_TYPE
from . import formats as _formats
from . import library as _library
from . import mapping as _mapping
from . import ncbi as _ncbi
from . import orfs as _orfs
from . import primers as _primers
from . import sanger as _sanger
from . import submission as _submission
from . import transfer as _transfer
from . import conda as _conda
from . import run_details as _run_details
from . import align_io as _align_io
from . import distance_tree as _distance_tree


def setting(name):
    return _config.setting(name)


def formats_read_xmfa(text):
    return _formats.read_xmfa(text)


# ── Alignment and tree programs' input and output (align_io.py, distance_tree.py) ──

def alignment_from_items(items):
    """Library sequences without gaps, under opaque ids: (records, rows)."""
    return _align_io.from_items(items)


def alignment_from_item(item):
    """An alignment item's rows under opaque ids: (records, rows)."""
    return _align_io.from_alignment(item)


def alignment_back(path, rows, keep_residues=None, program="The program"):
    """A program's aligned FASTA, checked, as records under the rows' names in input order."""
    return _align_io.back(path, rows, keep_residues, program)


def alignment_sources(rows):
    return _align_io.sources(rows)


def residues(text):
    return _align_io.residues(text)


def tree_rows(item, pattern=r"[^A-Za-z0-9_.-]+"):
    """An alignment's records renamed for a tree program, and {row name: new name}."""
    return _align_io.tree_rows(item, pattern)


def nexus_alignment(records, protein):
    """Aligned records as a NEXUS DATA block's text."""
    return _formats.write_alignment(records, "nexus", protein)


def newick_from_nexus(text):
    """The first tree of a NEXUS trees file as plain Newick, supports from its comments."""
    return _formats.newick_text(_formats.read_trees(text, "nexus")[0])


def distance_tree(names, sequences, **options):
    """A neighbour-joining or UPGMA tree as Newick, and notes for the log (distance_tree.py)."""
    return _distance_tree.build(names, sequences, **options)


def add_blast(results, name):
    return _library.add_blast(results, name)


def add_comparison(result, name, xmfa):
    return _library.add_comparison(result, name, xmfa)


def add_folder(name, parent=None):
    return _library.add_folder(name, parent)


def add_mapping(result, name, bam=None, reads=None):
    return _library.add_mapping(result, name, bam, reads)


def add_primers(result, name):
    return _library.add_primers(result, name)


def apply_annotation_group(results, busy):
    return _library.apply_annotation_group(results, busy)


def folder_of(item):
    return _library.folder_of(item)


def free_folder_name(name, parent=None):
    return _library.free_folder_name(name, parent)


def get_item(item_id):
    return _library.get_item(item_id)


def graph_data(item):
    return _library.graph_data(item)


def records(item):
    return _library.records(item)


def safe_name(text):
    return _library.safe_name(text)


def set_graph_hits(item, result):
    return _library.set_graph_hits(item, result)


def trace_data(item):
    return _library.trace_data(item)


def mapping_clipped_reads(sam_path):
    return _mapping.clipped_reads(sam_path)


def mapping_count_reads(path):
    return _mapping.count_reads(path)


def mapping_ends_reference(seq):
    return _mapping.ends_reference(seq)


def mapping_ends_script(groups, inside, cores=1, read_type='illumina'):
    return _mapping.ends_script(groups, inside, cores, read_type)


def mapping_extend_ends(reads, lengths, min_depth, min_agree):
    return _mapping.extend_ends(reads, lengths, min_depth, min_agree)


def mapping_merge_origin(per_position, insertions, ext_positions, ext_insertions, raw, length):
    return _mapping.merge_origin(per_position, insertions, ext_positions, ext_insertions, raw, length)


def mapping_mounts_for(groups):
    return _mapping.mounts_for(groups)


def mapping_read_clips(path, length):
    return _mapping.read_clips(path, length)


def mapping_read_consensus(ref_path, ins_path, length):
    return _mapping.read_consensus(ref_path, ins_path, length)


def mapping_read_counts(path, length, min_depth):
    return _mapping.read_counts(path, length, min_depth)


def mapping_read_depth(path, length):
    return _mapping.read_depth(path, length)


def mapping_read_reads(path, length, circular, stride=1):
    return _mapping.read_reads(path, length, circular, stride)


def mapping_read_stats(path):
    return _mapping.read_stats(path)


def mapping_script(groups, inside, length, min_depth, min_mapq, extend=0, cores=1, read_type='illumina'):
    return _mapping.script(groups, inside, length, min_depth, min_mapq, extend, cores, read_type)


def mapping_summary(reference_seq, depth, per_position, insertions, stats, min_depth, groups, variable=None):
    return _mapping.summary(reference_seq, depth, per_position, insertions, stats, min_depth, groups, variable)


def mapping_write_reads(path, reads, length, total=None):
    return _mapping.write_reads(path, reads, length, total)


def ncbi_identity():
    return _ncbi.identity()


def ncbi_pause():
    return _ncbi.pause()


def ncbi_post(url, params, timeout=60):
    """A request to NCBI, spaced to its limit and retried after brief server errors."""
    return _ncbi.post(url, params, timeout)


def orfs_find_orfs(sequence, code=1, min_codons=100, both_strands=True, any_start=False, circular=False):
    return _orfs.find_orfs(sequence, code, min_codons, both_strands, any_start, circular)


def orfs_overlaps(orf, cds, n):
    return _orfs.overlaps(orf, cds, n)


def primers_circular_design_template(sequence, target_start=None, target_end=None):
    return _primers.circular_design_template(sequence, target_start, target_end)


def primers_design_input(sequence, target_start=None, target_end=None, product_min=200, product_max=1000, tm_min=57.0, tm_opt=60.0, tm_max=63.0, gc_min=30.0, gc_max=70.0, num_return=5):
    return _primers.design_input(sequence, target_start, target_end, product_min, product_max, tm_min, tm_opt, tm_max, gc_min, gc_max, num_return)


def primers_map_design(pairs, rotation, length, circular=False):
    return _primers.map_design(pairs, rotation, length, circular)


def primers_normalize(primer):
    return _primers.normalize(primer)


def primers_parse_design(text):
    return _primers.parse_design(text)


def primers_test_pair(sequence, forward, reverse, mismatches=0, product_min=50, product_max=2000, circular=False, both_orientations=True, three_prime=None):
    return _primers.test_pair(sequence, forward, reverse, mismatches, product_min, product_max, circular, both_orientations, three_prime)


def sanger_assemble(reads):
    return _sanger.assemble(reads)


def submission_files(records, locus_tag_prefix='', drop_cladia_notes=True, dropped=None):
    return _submission.files(records, locus_tag_prefix, drop_cladia_notes, dropped)


def transfer_spans(locations, length):
    return _transfer._spans(locations, length)


def transfer_queries(record):
    return _transfer.queries(record)


def transfer_read_hits(text):
    return _transfer.read_hits(text)


def transfer_record_facts(feature):
    return _transfer.record_facts(feature)


def transfer_same_feature(a, b):
    return _transfer.same_feature(a, b)


def transfer_transfer(reference, target, circular, hits, lengths, limit, existing=(), code=1):
    return _transfer.transfer(reference, target, circular, hits, lengths, limit, existing, code)

TRANSFER_FIELDS = _transfer.FIELDS

MAPPING_READ_TYPES = _mapping.READ_TYPES

MAPPING_COUNT_AWK = _mapping.COUNT_AWK

MAPPING_CLIP_AWK = _mapping.CLIP_AWK

MAPPING_EXTEND = _mapping.EXTEND

MAPPING_MAX_LISTED = _mapping.MAX_LISTED

MAPPING_BASE_QUALITY = _mapping.BASE_QUALITY

MAPPING_READS_MAX = _mapping.READS_MAX

SANGER_NO_QUALITY = _sanger.NO_QUALITY




def tools_image():
    return _config.TOOLS_IMAGE


# ── Optional tools from bioconda (conda.py): ENGINE["conda"] = {"version", "spec"} ──

def conda_install(job, workdir, engine):
    """Downloads the engine's tool into its own environment (from its lock file)."""
    return _conda.install(job, workdir, engine["id"], engine["conda"]["version"])


def conda_uninstall(job, workdir, engine):
    return _conda.uninstall(job, workdir, engine["id"])


def conda_command(engine, args):
    """args run with the engine's environment first on the PATH (pass to run_container)."""
    return _conda.command(engine["id"], engine["conda"]["version"], args)


def private_volume(name):
    """A volume name of this data folder's own (another library's install or uninstall can't touch it)."""
    return f"cladia-{name}-{_conda.data_key()}"


def read_mounts(groups):
    return _mapping.mounts_for(groups)


def local_databases():
    return _blastdb.databases()


def tool_setting(engine_id, key, default=None):
    from .tool_registry import tool_setting as read_setting
    value = read_setting(engine_id, key)
    return default if value is None else value


def record_tool(job, name, version, citations=()):
    return _run_details.record_tool(job, name, version, citations)


def run_container(job, image, args, workdir, mounts=None, entrypoint=None):
    kwargs = {}
    if mounts is not None: kwargs['mounts'] = mounts
    if entrypoint is not None: kwargs['entrypoint'] = entrypoint
    return _execution.tool(job, image, args, workdir, **kwargs)


def execute_process(image, args, workdir, name=None, mounts=None, entrypoint=None):
    return _execution.run_container(image, args, workdir, name, mounts, entrypoint)


def tool_watched(job, args, workdir, watch, stdout=False):
    return _execution.tool_watched(job, args, workdir, watch, stdout)


def ensure_image(job, image):
    return _execution.ensure_image(job, image)


def ensure_tools_image(job):
    return _execution.ensure_tools_image(job)


def volume_create(name):
    return _execution.volume_create(name)


def volume_remove(name):
    return _execution.volume_remove(name)


def set_option(args, flag, value):
    return _execution.set_option(args, flag, value)


def in_parallel(job, image, workdir, commands, labels):
    return _execution.in_parallel(job, image, workdir, commands, labels)


def named_record(item, used):
    return _outputs.named_record(item, used)


def annotated(job, workdir, results, suffix, into, apply=False, busy=None):
    return _outputs.annotated(job, workdir, results, suffix, into, apply, busy)


def add_output(job, path, name, kind=None, extra=None, source=None):
    return _outputs.add_output(job, path, name, kind, extra, source)


def attach_alignment_variants(job, item, vcf_text, reference, expected_source_hash, save_copy=False):
    return _outputs.attach_alignment_variants(job, item, vcf_text, reference, expected_source_hash, save_copy)


def create_result_folder(job, name):
    return _outputs.create_result_folder(job, name)


def watch_log(job, path, reader, offset=0):
    return _progress.watch_log(job, path, reader, offset)


def steps(job, *names):
    return _progress.steps(job, *names)


def step(job, at, fraction=None, detail=''):
    return _progress.step(job, at, fraction, detail)

__all__ = [
    'BLAST_PROGRAMS', 'LOCAL_DB_TYPE', 'MAPPING_BASE_QUALITY', 'MAPPING_CLIP_AWK',
    'MAPPING_COUNT_AWK', 'MAPPING_EXTEND', 'MAPPING_MAX_LISTED', 'MAPPING_READS_MAX',
    'MAPPING_READ_TYPES', 'SANGER_NO_QUALITY', 'TRANSFER_FIELDS', 'add_blast',
    'add_comparison', 'add_folder', 'alignment_back', 'alignment_from_item', 'alignment_from_items',
    'alignment_sources', 'distance_tree', 'newick_from_nexus', 'nexus_alignment', 'residues', 'tree_rows','add_mapping', 'add_output', 'attach_alignment_variants',
    'add_primers', 'annotated', 'apply_annotation_group', 'conda_command', 'conda_install',
    'conda_uninstall', 'create_result_folder',
    'editor_to_record', 'ensure_image', 'ensure_tools_image', 'execute_process',
    'file_format', 'folder_of', 'formats_read_xmfa', 'free_folder_name',
    'genetic_code', 'get_item', 'graph_data',
    'in_parallel', 'local_databases', 'looks_protein', 'mapping_clipped_reads',
    'mapping_count_reads', 'mapping_ends_reference', 'mapping_ends_script', 'mapping_extend_ends',
    'mapping_merge_origin', 'mapping_mounts_for', 'mapping_read_clips', 'mapping_read_consensus',
    'mapping_read_counts', 'mapping_read_depth', 'mapping_read_reads', 'mapping_read_stats',
    'mapping_script', 'mapping_summary', 'mapping_write_reads', 'named_record',
    'ncbi_identity', 'ncbi_pause', 'ncbi_post','orfs_find_orfs', 'orfs_overlaps',
    'parse_blast', 'private_volume', 'primers_circular_design_template', 'primers_design_input', 'primers_map_design',
    'primers_normalize', 'primers_parse_design', 'primers_test_pair', 'read_mounts',
    'read_records', 'record_to_editor', 'record_tool', 'records', 'rotated_feature',
    'run_container', 'safe_name', 'sanger_assemble', 'set_graph_hits',
    'set_option', 'setting', 'step',
    'steps', 'submission_files', 'tool_setting', 'tool_watched',
    'tools_image', 'trace_data', 'transfer_queries', 'transfer_read_hits',
    'transfer_record_facts', 'transfer_same_feature', 'transfer_spans', 'transfer_transfer',
    'turned', 'volume_create', 'volume_remove', 'watch_log',
    'write_fasta', 'write_record',
]
