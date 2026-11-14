# Plan: optional tools from bioconda

Written 6 November 2026. **Implemented the same day, more fully than staged below** (the user's choice: as if plugins had never existed): the plugin system is gone; Clustal Omega, GetOrganelle and Krait (pytrf) are optional engines in `cladia/tool_plugins/` that download from bioconda; Alignment variants is built in; GitHub plugins are not kept for anything. fastp (stage 1) waits for the Reads button decisions. The stages below are the original plan, kept as the record.

## Why

Most analysis tools Cladia will add (fastp, bcftools, SPAdes, Flye, Bowtie2, BWA,
MrBayes, RAxML-NG, tRNAscan-SE, barrnap…) need no code of their own beyond an
adapter. Publishing each as a GitHub plugin costs a repository, tagged releases, a
catalog entry, the release gate and, when Cladia redistributes the binary, its
licence duties (Clustal Omega's ZIP carries sources and build notes for that).

Instead:

- the **adapter** (an engine module) ships with Cladia, in `cladia/tool_plugins/`,
  versioned and tested with the app;
- the **tool** is downloaded from **bioconda / conda-forge** when the user installs
  it in Settings › Tools, at an exact version, every package checked by checksum;
- **GitHub plugins remain** for code that is Cladia's own and not an upstream tool
  (Alignment variants, Krait's worker), for lab-specific or third-party tools, and
  for anything that must update faster than the app.

To the user nothing changes: Settings › Tools lists built-in, optional and plugin
tools alike; installing is a job; the tool's tab appears when it succeeds.

## Decisions (agreed or proposed)

1. **Channels: conda-forge and bioconda only**, never Anaconda's `defaults`
   (commercial licence terms). Every environment is created with
   `--override-channels -c conda-forge -c bioconda`, and lock files must contain no
   `repo.anaconda.com` URL (a test checks this).
2. **micromamba**, not conda: one static binary, no base Python, fast solver.
3. **One environment per tool** (`/opt/cladia-conda/envs/<engine id>-<version>`),
   so tools never share or upgrade each other's libraries. The package cache
   (`/opt/cladia-conda/pkgs`) hardlinks shared packages, so disk use stays moderate.
4. **Exact, reproducible installs**: each engine ships an explicit lock file
   (`@EXPLICIT` format: one package URL and MD5/SHA-256 per line) generated once per
   tool version. Installs never run the solver on the user's PC.
5. **Lives in the shared `tools` distribution** (Cladia's Linux root), not a new
   isolated root: conda environments already isolate libraries. The existing
   `getorganelle` isolated root is left alone until its migration.
6. **x86-64 Linux only** for lock files at first (`linux-64`). ARM64 Windows
   machines get "not available on this computer" until `linux-aarch64` locks exist.
7. **No automatic upgrades.** A new tool version = a new lock file in a Cladia
   release; Settings offers *Update* when the installed lock differs from the
   shipped one. Results keep the version they were made with (provenance).

## Architecture

### The runtime (new module `cladia/conda.py`)

```
MICROMAMBA_VERSION = "2.x.y"                 # pinned
MICROMAMBA_URL     = ".../micromamba-linux-64"  # conda-forge's micromamba package, or its GitHub release
MICROMAMBA_SHA256  = "…"
ROOT = "/opt/cladia-conda"                   # on the tools root
```

Functions (all run on the Linux side through `linux.py`/`execution.py`):

- `ensure_micromamba(job)`: downloads the binary to `ROOT/bin/micromamba` if it is
  missing or its checksum differs; checks `micromamba --version`. Idempotent.
- `env_path(engine_id, version) -> str`.
- `installed_lock(engine_id) -> str | None`: the lock file's SHA-256 recorded in
  `ROOT/envs/<id>-<version>/.cladia-lock`.
- `create_env(job, engine_id, version, lock_text)`: writes the lock into the job
  folder, then
  `micromamba create -y -p <env>.partial --override-channels -c conda-forge -c bioconda -f /data/lock.txt`,
  renames `<env>.partial` to `<env>` only on success (an interrupted install leaves
  no half environment), writes `.cladia-lock`. Progress: micromamba's download and
  link lines read by a watcher, like `_watch_map`.
- `remove_env(job, engine_id, version)`.
- `command(engine_id, version, args) -> list`: the command that runs a program from
  the environment, `["env", f"PATH={env}/bin:/usr/bin:/bin", *args]`. Prefer that
  over `micromamba run` (no activation scripts, no shell state).
- `clean_cache(job)`: `micromamba clean -a -y` after installs, so the cache
  doesn't keep every tarball.

Expose through `engine_api.py` (plugins must only use `engine_api`):
`conda_install`, `conda_uninstall`, `conda_command`, `conda_installed`.

### An engine that uses it

```python
CONDA = {"version": "0.24.0", "lock": "fastp-0.24.0.lock"}   # lock beside the module

ENGINE = {
    "id": "fastp", "label": "fastp", "optional": True, "conda": CONDA,
    "storage": {"kind": "linux", "environment": "tools",
                "paths": ["/opt/cladia-conda/envs/fastp-0.24.0"]},
    ...
}

def install(job, workdir, choices):
    api.conda_install(job, ENGINE["id"], CONDA)

def uninstall(job, workdir):
    api.conda_uninstall(job, ENGINE["id"], CONDA)

def run(job, workdir, ...):
    api.run_container(job, api.tools_image(), api.conda_command("fastp", CONDA, ["fastp", ...]), workdir, mounts=...)
```

Lock files live in `cladia/tool_plugins/locks/<id>-<version>.lock` (text, a few
kB). The engine's `CONDA` dict names it; `conda_install` reads it from there.

### Making lock files (maintainer step, `scripts/make_conda_lock.py`)

Run on this PC in Cladia's Linux (or any linux-64 machine):

1. `micromamba create -p /tmp/lock-env --override-channels -c conda-forge -c bioconda <spec>`
   (e.g. `fastp=0.24.0`; pin the build too when it matters).
2. `micromamba env export -p /tmp/lock-env --explicit --md5 > <id>-<version>.lock`.
3. Check: no `repo.anaconda.com` URL; every line has a checksum; size of the
   packages (written as `diskBytes` estimate in the engine).
4. Recreate a fresh environment from the lock and run the tool's version check.
5. Commit the lock with the engine change.

## Stages

Each stage ends with tests passing, a commit and a push. Stages 2 and 3 don't depend
on each other.

### Stage 1: the conda runtime and fastp

1. **`cladia/conda.py`** as above; pin micromamba (version, URL, SHA-256) in it.
2. **`engine_api.py`**: the four `conda_*` functions; add them to `__all__`.
3. **Uninstall for bundled optional engines.** Check `api_tools.py` /
   `tool_install.py`: install exists for bundled optional engines
   (`tool_install.install_engines`); add the matching uninstall route and job if it
   is missing (plugins have theirs in `plugin_packages.py`), calling
   `engine["uninstall"]` and dropping the id from the installed list
   (`save_installed_engine`). Settings › Tools' detail dialog shows *Uninstall* for
   installed bundled optional tools.
4. **Update**: `tool_registry` marks a bundled conda engine `outdated` when
   `conda.installed_lock(id)` differs from the shipped lock's hash; the detail dialog
   offers *Update* (an install job that creates the new environment, then removes
   the old one). Running jobs of that engine block the update, as for plugins.
5. **Storage**: `tool_storage.validate` accepts `/opt/cladia-conda/...` paths in the
   `tools` environment; Settings › Storage gets a *Conda package cache* line and a
   *Clean* button (`conda.clean_cache`).
6. **Installer**: `scripts/build_installer.py` currently makes optional-tool tasks
   from the plugin catalog only. Add bundled optional engines (read from
   `tool_registry.build_registry().installable()` filtered to optional, non-plugin)
   so the installer's optional-tool page offers them too.
7. **fastp engine** (`cladia/tool_plugins/fastp.py`) with its lock, supporting the
   Reads operations (see the Reads button design, decided separately):
   *Check* (report only) and *Trim* (new reads item). Its HTML/JSON report fills
   the reads report page.
8. **Tests** (`tests/test_conda.py`):
   - lock files: parse, every line `https://conda.anaconda.org/(conda-forge|bioconda)/…#<hash>`,
     no `defaults`;
   - `conda.command` builds the PATH-prefixed command;
   - install/uninstall/update with `linux.run` replaced: the partial-then-rename
     order, `.cladia-lock` written, failure leaves no environment;
   - registry: an optional conda engine is unavailable until installed, `outdated`
     when the lock changes;
   - installer task generation includes bundled optional engines.
9. **Real check** (`scripts/check_conda_tools.py`, private temporary library like
   the other `check_*` scripts): install fastp from the lock in the real Linux,
   `fastp --version` matches, run it on simulated paired reads, uninstall, confirm
   the environment folder is gone and Storage no longer counts it.
10. **Docs**: README/INSTALLATION (internet needed when installing optional
    tools), USER_GUIDE (Settings › Tools: Update, Uninstall), CLAUDE.md section,
    `docs/TOOL_VERSION_AUDIT.md` (fastp row: source bioconda, version, lock).

### Stage 2: Clustal Omega becomes a bundled conda tool

1. Make `clustalo-1.2.4.lock` (bioconda `clustalo=1.2.4`). Compare its output with
   the current build on the test alignments in `tests/test_clustal_plugin.py`
   (DNA, RNA, protein, IUPAC): identical alignments expected; if not, record the
   difference before going on.
2. Move `plugins/clustal_omega/engine.py` to `cladia/tool_plugins/clustal_omega.py`:
   - keep the same engine id `clustal_omega`, label, options, run logic, input-order
     restoration, checks and citations;
   - replace the private volume (`_volume`, `/tool/clustalo`) with
     `api.conda_command("clustal_omega", CONDA, ["clustalo", ...])`;
   - `install`/`uninstall` become the conda ones; `ENGINE["optional"] = True`.
3. **Migration of an existing install** (only the user's PCs have it):
   - at startup, if `.cladia/plugins/installed.json` lists `clustal_omega`, remove
     that plugin record and its package folder (`plugin_packages` uninstall path,
     which also removes the private volume), and **do not** mark the bundled engine
     installed: Settings shows *Install* again, with a one-line note in the log.
     (The plugin id equals the engine id; registry discovery must never load both:
     check `tool_registry` refuses external ids that duplicate bundled ones, which
     it already does.)
   - existing alignments keep their provenance; nothing in the library changes.
4. Remove from the repository: `plugins/clustal_omega/` (build.py, build.sh,
   upstream.json, COPYING/NOTICE copies), `build/clustal-omega/` references, its
   catalog entry and `bundledAsset` ZIP in `cladia/plugin_catalog.json` /
   `cladia/bundled_plugins/`, `scripts/check_clustal_plugin.py` (replace with a
   section of `check_conda_tools.py`), the Clustal parts of
   `docs/PLUGIN_PUBLISHING.md`'s release gate.
5. Tests: rewrite `tests/test_clustal_plugin.py` as `tests/test_clustal.py` for the
   bundled engine (container calls replaced as today); keep
   `scripts/test_clustal_ui.cjs` working (it installs from Settings).
6. Docs: CLAUDE.md's Clustal paragraph (it describes the plugin build), README,
   USER_GUIDE, TOOL_VERSION_AUDIT.

### Stage 3: GetOrganelle becomes a bundled conda tool (do with real Illumina data)

GetOrganelle still has never run on real data. Move it when that test is done, so it
is validated once, on the new install.

1. Make `getorganelle-1.7.7.1.lock` (bioconda `getorganelle=1.7.7.1`; it brings
   SPAdes, Bowtie2, BLAST+ as dependencies). Check `get_organelle_from_reads.py -v`.
2. Move `plugins/getorganelle/engine.py` to `cladia/tool_plugins/getorganelle.py`:
   - `BIN = "/opt/cladia-getorganelle/bin"` and `api.getorganelle_image()` go;
     commands use `api.conda_command("getorganelle", CONDA, [...])` in the tools root;
   - databases: keep the private volume mounted at the same `DB_PATH`, so
     `get_organelle_config.py --add` and runs see them as before; `install(job, workdir,
     choices)` creates the environment, then adds the chosen databases (as now);
     adding more later stays the same `installChoices` flow;
   - the live log registration (`assembly/get_org.log.txt`), cores via `-t`, and the
     log dialog stay exactly as they are.
3. **Migration**: if the `getorganelle` plugin is installed, keep its database
   volume (rename it to the bundled engine's volume name if they differ, or point
   the engine at the existing one) and mark the bundled engine installed with the
   same database choices, so no re-download is needed; remove the plugin record and
   package. Then remove the old isolated root (`linux.DISTROS["getorganelle"]`,
   `/var/lib/cladia/environments/getorganelle`) once no job uses it, following the
   existing "retire legacy" pattern in `linux.py` (`_retire_legacy`).
4. Remove: `containers/getorganelle/` (setup.sh, Dockerfile),
   `config.GETORGANELLE_VERSION`, `engine_api.getorganelle_image`,
   `linux.GETORGANELLE_SETUP` and the `getorganelle` DISTROS entry (after the
   migration code that retires it), the plugin catalog entry and bundled ZIP,
   `plugins/getorganelle/`.
5. Real test: assemble one real Illumina sample (from a reads item, now possible);
   compare with the lab's own GetOrganelle result on the HPC if there is one.
6. Docs: CLAUDE.md's GetOrganelle paragraphs, USER_GUIDE, INSTALLATION, the
   plugin publishing doc (two plugins left: Alignment variants and Krait).

### Stage 4: the plugin catalog, smaller

With Clustal and GetOrganelle bundled, the release gate in
`docs/PLUGIN_PUBLISHING.md` covers two plugins (Alignment variants, Krait). Update
the gate's list, `cladia/plugin_catalog.json`, and the review doc's *Before a public
release* item. Nothing else in the plugin system changes.

### Afterwards: phase 2 tools

Each new tool is then: make the lock (`scripts/make_conda_lock.py`), write the
engine module with `"conda"`, add its options and operations, a test with the
container replaced, a section in `check_conda_tools.py`, and a row in
TOOL_VERSION_AUDIT. Candidates, in roadmap order: bcftools, SPAdes / Unicycler,
Flye, Bowtie2, BWA, MrBayes, tRNAscan-SE, ARAGORN, barrnap, MUSCLE 5, RAxML-NG.
(Tools already built into the tools root, such as MAFFT, IQ-TREE, BLAST+ and
samtools, stay as they are: moving them gains nothing.)

## Risks and how they are handled

| Risk | Handling |
| --- | --- |
| A package disappears from the channel | Explicit URLs fail clearly at install with the package named; fix by a new lock in an app update. Keep an eye on it in `check_conda_tools.py` runs before releases. |
| A tool's bioconda build differs from upstream's | Compare outputs on the existing test data when migrating (Clustal); record the version actually reported in provenance, as now. |
| Disk use (each environment brings libraries) | One cache with hardlinks; `clean_cache` after installs; sizes shown in Settings › Storage. |
| No internet in the lab | Same as plugins today: optional tools need a download once. An offline bundle (the lock's packages in a ZIP) could be added later if asked. |
| Anaconda licence terms | `defaults` never used; test enforces it on every lock. |
| ARM64 Windows | Locks for linux-64 only at first; the tool says it isn't available on this computer. |
| Interrupted install | `<env>.partial`, renamed only on success; a leftover `.partial` is deleted at the next install. |

## Order and rough size

| Stage | Work | Depends on |
| --- | --- | --- |
| 1. Runtime + fastp | the main part (runtime, engine API, uninstall/update, installer, fastp, tests) | the Reads button design for fastp's operations |
| 2. Clustal Omega | small | stage 1 |
| 3. GetOrganelle | moderate; mostly migration and the real-data test | stage 1, real Illumina reads |
| 4. Catalog | small | stages 2–3 |

## Open questions for the user

1. The Reads button: does Map move under it (tabs Check / Trim / Map), and do
   trimmed reads become a new reads item beside the original (proposed) or replace it?
2. Which fastp options to show (proposed: quality threshold, minimum length, adapter
   detection for pairs, merging pairs off by default, deduplication off).
3. Should the Windows installer's optional-tool page list the conda tools (fastp,
   Clustal, GetOrganelle) so a lab can preinstall them, or only Settings?
