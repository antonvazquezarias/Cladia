# License audit for a public release (13 November 2026)

What Cladia contains, downloads or runs, and whether each part allows a public
GitHub repository. Checked from the code, the conda locks, the installed venv,
the MFannot image and upstream repositories. Not legal advice.

## Summary

| Area | Status |
|---|---|
| Cladia's own code | Original; no third-party code copied in. **MIT License** (`LICENSE`, chosen 13 Nov 2026). |
| Python packages (pip) | All permissive or LGPL; installed by setup.bat, not in the repository. OK. |
| Tools in Cladia's Linux | Open source or public domain; downloaded on the user's PC. OK. |
| Optional bioconda tools | Open source; downloaded on the user's PC. OK. |
| Installer | Contains only the repository's own files. OK. |
| Test data | Synthetic only (`tests/make_test_data.py`). OK. |
| Git history | Earlier bundled tools carried their licenses and sources. OK, but the history contains the same test data. |
| MFannot image | Published by MFannot's authors (GPL-3.0, plus Erpin, which has no license); pulled on the user's PC, not redistributed by Cladia. OK. |

## Cladia's own code

Cladia runs every external program as a separate process (WSL, a container or
micromamba). It does not link to or copy GPL code. The Python packages it imports
are permissive, apart from svglib (LGPL-3.0, imported unmodified and installed
separately, which the LGPL allows). Cladia can therefore be released under any
license: MIT/BSD-3-Clause (simplest) or GPL-3.0 (keeps derivatives open).

Chosen: MIT (the most permissive simple license; the tools' GPL/AGPL don't reach
Cladia because it only runs them). `LICENSE` at the root and a License section in
the README.

## Python packages (requirements.txt, installed by setup.bat)

| Package | License |
|---|---|
| biopython 1.88 | Biopython License Agreement (BSD-like) |
| numpy | BSD-3-Clause |
| fastapi, pydantic, pydantic_core, annotated-types, anyio, h11 | MIT |
| starlette, uvicorn, click, idna | BSD-3-Clause |
| pywebview | BSD-3-Clause |
| pythonnet, clr_loader, proxy_tools, bottle | MIT |
| svglib | LGPL-3.0-or-later |
| reportlab | BSD |
| lxml, cssselect2, tinycss2, webencodings | BSD |
| pillow | MIT-CMU (HPND) |
| openpyxl, et_xmlfile | MIT |
| markdown | BSD-3-Clause |
| typing_extensions | PSF-2.0 |
| pytest, httpx2 (development only) | MIT, BSD-3-Clause |

## Tools installed in Cladia's Linux (setup.sh, upstream.sh)

Downloaded from upstream or Debian at first use; never in the repository or installer.

| Tool | License |
|---|---|
| Debian bookworm base | Mixed free licenses (Debian) |
| MAFFT 7.526 | BSD (core) |
| IQ-TREE 3.1.4 | GPL-2.0 |
| FastTree 2.2 | GPL (repository: GPL-3.0) |
| minimap2 2.31 | MIT |
| samtools / HTSlib 1.25 | MIT / MIT + BSD |
| BLAST+ 2.17.0, table2asn | Public domain (NCBI, US Government work) |
| progressiveMauve (Debian) | GPL-2.0 |
| Primer3 (Debian) | GPL-2.0 |
| micromamba 2.9.0 | BSD-3-Clause |

## Optional tools (bioconda locks in cladia/tool_plugins/locks)

All locks use only conda-forge and bioconda (community channels, not Anaconda's
`defaults`, so Anaconda's commercial terms don't apply). Main programs:

| Tool | License |
|---|---|
| Clustal Omega 1.2.4 | GPL-2.0 |
| MUSCLE 5.3 | GPL-3.0 |
| MACSE 2.07 | CeCILL-2.1 (GPL-compatible) |
| RAxML-NG 2.0.4 | AGPL-3.0 (unmodified, run as a program: no obligation for Cladia) |
| MrBayes 3.2.9, beagle-lib | GPL-3.0 |
| tRNAscan-SE 2.0.13, Infernal | GPL-3.0, BSD-3-Clause |
| ARAGORN 1.2.41 | GPL-3.0 |
| trimAl 1.5.1 | GPL-3.0 |
| ClipKIT 2.14.0 | MIT |
| GetOrganelle 1.7.7.1 (+ SPAdes, Bowtie2, BLAST+) | GPL-3.0 (SPAdes GPL-2.0, Bowtie2 GPL-3.0) |
| Krait = pytrf 1.5.0 + pyfastx 2.3.1 | MIT |

The ~120 conda-forge dependencies (Python, OpenJDK, Boost, OpenBLAS, GCC
runtimes...) are all open-source licenses (full list checked through the
anaconda.org API; none proprietary or "unknown").

## MFannot image (nbeck/mfannot, pulled on the user's PC)

The MFannot engine pulls the MFannot authors' own Docker Hub image, pinned by digest
(`sha256:b71c55d2…`, built Feb 2021 from BFL-lab's repositories): MFannot,
RNAfinder, mf2sqn and their tools (GPL-3.0), and Erpin. Cladia's repository and
installer contain none of it; the authors distribute the image, including Erpin.

## Repository content

- `tests/data` holds no real data since 14 Nov 2026 (the user's wish):
  `synthetic_mt.gb` and the two .ab1 traces are written by
  `tests/make_test_data.py` (random sequence, invented identifiers); the two GFA
  graphs keep the shape of the lab's graphs with random sequences. The earlier
  files (Biopython's example traces, the *C. reinhardtii* mitochondrion and the
  lab's graphs) were removed.
- `mafft-*-progress.log`: logs from synthetic sequences. OK.
- `cladia/static/cladia.ico`, `installer/cladia-logo.png`: Cladia's own logo
  (scripts/make_icon.py). OK.
- No vendored JavaScript (`cladia/static/vendor` is empty; OVE and phylotree.js
  were removed before git was used).

## Git history

The removed plugin packages are still in the history:
`clustal-omega-1.0.0.zip` shipped the clustalo binary together with its sources,
COPYING and the argtable2/SQUID licenses (GPL-compliant). Krait's packages
contained the embedded CPython (PSF license, its LICENSE.txt included) and the
pytrf/pyfastx wheels (MIT). The other packages were Cladia code only. Nothing
needs to be removed for licensing reasons. The history does contain the earlier
real test data; a public repository started from a fresh snapshot avoids it. The history is also 81 MB.

## Before making the Cladia repository public

1. ~~Cladia's LICENSE~~ (MIT, done).
2. ~~The lab data in `tests/data`~~ (replaced by synthetic data); start the public
   repository from a fresh snapshot.
3. ~~`tests/data/README.md`~~ (done).
4. ~~A README section pointing to this file and the citations~~ (done).
