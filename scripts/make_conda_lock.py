"""Make the lock file of an optional tool from bioconda (a maintainer step).

    python scripts/make_conda_lock.py <engine id> <version> "<package spec>" ["<check command>"]
    python scripts/make_conda_lock.py clustal_omega 1.2.4 "clustalo=1.2.4" "clustalo --version"

Runs in Cladia's own Linux (it must exist: run any tool once first). Creates a
scratch environment from conda-forge and bioconda only, exports it as an
explicit lock (each package's URL and MD5), checks no package comes from
Anaconda's defaults channel, makes a second environment from the lock alone,
runs the check command in it, and writes
cladia/tool_plugins/locks/<engine id>-<version>.lock. Commit the lock with the
engine change. See docs/PLAN_BIOCONDA_TOOLS.md.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cladia import conda, linux  # noqa: E402


def sh(script):
    proc = subprocess.run([str(linux.WSL), "-d", linux.DISTRO, "-u", "root", "--exec", "bash", "-c", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        sys.exit(f"Failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def main(engine, version, spec, check=None):
    if not re.fullmatch(r"[a-z0-9_]+", engine) or not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        sys.exit("Engine ids are lower-case letters, digits and _; versions plain")
    sh(conda.ensure_script())
    m = f"MAMBA_ROOT_PREFIX={conda.ROOT} {conda.MICROMAMBA}"
    scratch, again = f"/tmp/cladia-lock-{engine}", f"/tmp/cladia-lock-{engine}-check"
    lock = sh(f"rm -rf {scratch} && {m} create -y -q -p {scratch} {conda.CHANNELS} {spec} >/dev/null && "
              f"{m} env export -p {scratch} --explicit --md5")
    lock = "\n".join(line for line in lock.splitlines() if line.startswith(("@EXPLICIT", "https://", "# platform"))) + "\n"
    problems = conda.check_lock(lock)
    if problems:
        sys.exit("Lock refused:\n" + "\n".join(problems))
    sh(f"rm -rf {again} && cat > /tmp/cladia-lock.txt <<'EOF'\n{lock}EOF\n"
       f"{m} create -y -q -p {again} {conda.CHANNELS} -f /tmp/cladia-lock.txt >/dev/null")
    if check:
        print(sh(f"PATH={again}/bin:/usr/bin:/bin {check}").strip())
    size = sh(f"du -sb {again} | cut -f1").strip()
    sh(f"rm -rf {scratch} {again} /tmp/cladia-lock.txt")
    out = ROOT / "cladia" / "tool_plugins" / "locks" / f"{engine}-{version}.lock"
    out.parent.mkdir(exist_ok=True)
    out.write_text(lock, encoding="utf-8", newline="\n")
    print(f"Wrote {out.relative_to(ROOT)}: {lock.count('https://')} packages, environment {int(size) / 1e6:.0f} MB")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    main(*sys.argv[1:])
