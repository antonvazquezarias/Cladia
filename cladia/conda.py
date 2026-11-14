"""Optional tools from bioconda, in Cladia's own Linux.

An optional tool's adapter (an engine module in tool_plugins/) ships with
Cladia; the tool itself is downloaded when the user installs it in Settings ›
Tools: micromamba creates an environment from the engine's lock file, an
explicit list of package URLs and checksums from conda-forge and bioconda only
(never Anaconda's `defaults` channel, which has commercial licence terms), so
every PC gets exactly the same packages and no solver runs on the user's PC.

    /opt/cladia-conda/bin/micromamba           pinned below, checked by SHA-256
    /opt/cladia-conda/pkgs/                    package cache (hardlinked into environments)
    /opt/cladia-conda/envs/<id>-<version>-<data folder key>/

An engine declares ENGINE["conda"] = {"version": "...", "spec": "..."}; its lock is
tool_plugins/locks/<id>-<version>.lock (scripts/make_conda_lock.py makes it). Each
Cladia data folder has its own environments (the key), so a scratch library can't
uninstall another library's tool; the package cache keeps the copies cheap. An
environment is complete when it holds .cladia-lock (the lock's hash), written last:
conda writes its own path into scripts, so environments are made in place, never
moved. See docs/PLAN_BIOCONDA_TOOLS.md.
"""

import hashlib
import re
import shlex
from pathlib import Path

from . import config

ROOT = "/opt/cladia-conda"
MICROMAMBA = f"{ROOT}/bin/micromamba"
MICROMAMBA_VERSION = "2.9.0"
MICROMAMBA_URL = "https://github.com/mamba-org/micromamba-releases/releases/download/2.9.0-0/micromamba-linux-64"
MICROMAMBA_SHA256 = "366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3"
CHANNELS = "--override-channels -c conda-forge -c bioconda"
LOCKS = Path(__file__).resolve().parent / "tool_plugins" / "locks"
_PACKAGE = re.compile(r"https://conda\.anaconda\.org/(conda-forge|bioconda)/(linux-64|noarch)/[A-Za-z0-9_.+-]+\.(conda|tar\.bz2)#[0-9a-f]{32,64}")


# ── Lock files ──

def check_lock(text):
    """What is wrong with a lock file ([] when nothing): an explicit list of
    conda-forge / bioconda packages, each with its checksum."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    problems = [] if lines[:1] == ["@EXPLICIT"] else ["it doesn't start with @EXPLICIT"]
    packages = lines[1:]
    if not packages:
        problems.append("it lists no packages")
    problems += [f"not a conda-forge or bioconda package with a checksum: {line}" for line in packages if not _PACKAGE.fullmatch(line)]
    return problems


def lock_path(engine_id, version):
    return LOCKS / f"{engine_id}-{version}.lock"


def lock_text(engine_id, version):
    text = lock_path(engine_id, version).read_text(encoding="utf-8")
    problems = check_lock(text)
    if problems:
        raise ValueError(f"The lock file of {engine_id} is not valid: {problems[0]}")
    return text


def lock_hash(engine_id, version):
    return hashlib.sha256(lock_text(engine_id, version).encode("utf-8")).hexdigest()


# ── Where things are ──

def data_key():
    """This data folder's short key (its environments are its own)."""
    return hashlib.sha256(str(Path(config.DATA_DIR).resolve()).lower().encode("utf-8")).hexdigest()[:12]


def env_path(engine_id, version):
    return f"{ROOT}/envs/{engine_id}-{version}-{data_key()}"


def command(engine_id, version, args):
    """A program of the tool's environment, run without activating it: its bin first on the PATH."""
    return ["env", f"PATH={env_path(engine_id, version)}/bin:/usr/local/bin:/usr/bin:/bin", *map(str, args)]


# ── Scripts run in Cladia's Linux ──

def ensure_script():
    """micromamba in place, checked (downloaded when missing or not the pinned one)."""
    return "\n".join([
        "set -e", f"mkdir -p {ROOT}/bin {ROOT}/envs {ROOT}/pkgs",
        f'if [ "$(sha256sum {MICROMAMBA} 2>/dev/null | cut -d" " -f1)" != "{MICROMAMBA_SHA256}" ]; then',
        f"  curl -fsSL --retry 3 -o {MICROMAMBA}.download {MICROMAMBA_URL}",
        f'  [ "$(sha256sum {MICROMAMBA}.download | cut -d" " -f1)" = "{MICROMAMBA_SHA256}" ] || '
        f'{{ rm -f {MICROMAMBA}.download; echo "micromamba download checksum mismatch" >&2; exit 1; }}',
        f"  chmod 755 {MICROMAMBA}.download && mv -f {MICROMAMBA}.download {MICROMAMBA}",
        "fi",
    ])


def install_script(engine_id, version, lock_sha):
    """The tool's environment made from /data/tool.lock (in place, any earlier one of
    this tool and data folder removed); .cladia-lock written last."""
    env = env_path(engine_id, version)
    others = f"{ROOT}/envs/{engine_id}-*-{data_key()}"
    return "\n".join([
        ensure_script(),
        f'if [ "$(cat {shlex.quote(env)}/.cladia-lock 2>/dev/null)" = "{lock_sha}" ]; then echo "Already installed" >&2; exit 0; fi',
        f"export MAMBA_ROOT_PREFIX={ROOT}",
        f"for old in {others}; do [ -e \"$old\" ] && rm -rf \"$old\"; done",
        "echo 'Downloading packages' >&2",
        f"{MICROMAMBA} create -y -p {shlex.quote(env)} {CHANNELS} -f /data/tool.lock",
        f"echo {lock_sha} > {shlex.quote(env)}/.cladia-lock",
        f"{MICROMAMBA} clean -a -y -q >/dev/null 2>&1 || true",  # (the cache keeps no tarballs)
        "echo 'Installed' >&2",
    ]) + "\n"


def uninstall_script(engine_id):
    return f"for old in {ROOT}/envs/{engine_id}-*-{data_key()}; do [ -e \"$old\" ] && rm -rf \"$old\"; done; true\n"


# ── For engines (through engine_api) ──

def install(job, workdir, engine_id, version):
    """Downloads and makes the tool's environment (a step of an install job)."""
    from . import execution
    text = lock_text(engine_id, version)
    (workdir / "tool.lock").write_text(text, encoding="utf-8", newline="\n")
    (workdir / "conda-install.sh").write_text(install_script(engine_id, version, lock_hash(engine_id, version)), newline="\n")
    job["log"] += f"Installing {engine_id} {version} from conda-forge / bioconda ({text.count('https://')} packages, exact versions)\n"
    execution.ensure_tools_image(job)
    execution.tool(job, config.TOOLS_IMAGE, ["bash", "/data/conda-install.sh"], workdir)


def uninstall(job, workdir, engine_id):
    from . import execution
    (workdir / "conda-uninstall.sh").write_text(uninstall_script(engine_id), newline="\n")
    execution.tool(job, config.TOOLS_IMAGE, ["bash", "/data/conda-uninstall.sh"], workdir)
