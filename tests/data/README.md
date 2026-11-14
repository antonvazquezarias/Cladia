# Test data

Everything here is synthetic or made by Cladia's own tests; no real sequencing
data. `python tests/make_test_data.py` writes the first three files again,
identically.

- `synthetic_mt.gb`: a made-up 15,789 bp mitochondrion-like genome named "Mt":
  random AT-rich sequence with 8 genes whose CDSs are real open reading frames,
  their mRNAs and UTRs, 3 tRNAs and 14 rRNA pieces, with the leftover GFF3
  qualifiers (ID, Dbxref) of a Geneious export. Gene names and positions follow
  a common mitochondrial layout; the IDs are invented.
- `read_qual.ab1`, `read_noqual.ab1`: synthetic Sanger traces (ABIF files) of two
  stretches of that genome: an "ABI 3100" read whose qualities are low at both
  ends, and an "ABI 310" read without qualities.
- `graph_small.gfa`, `graph_spades.gfa`: assembly graphs with the shape (node
  names, lengths, read counts and links) of two organelle assembly graphs, every
  sequence replaced with random bases; `graph_spades.gfa` keeps only three
  connected groups of a SPAdes graph.
- `mafft-auto-progress.log`, `mafft-reference-progress.log`: MAFFT 7.526 logs from
  62 synthetic sequences.
- `http_routes.json`: the API route snapshot (`tests/test_http_boundary.py`).
