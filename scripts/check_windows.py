"""Checks the Linux side of Cladia on a Windows PC. Run from the project folder:

    python scripts\\check_windows.py

Steps: find WSL -> make (or check) Cladia's tools Linux -> the tools answer ->
prepare each annotation engine's own image inside the same distribution (it runs the
engine's own check; MFannot's image is 1.7 GB) -> run MAFFT.
Each step prints OK or what went wrong.
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cladia import config, jobs, linux  # noqa: E402
import cladia.execution as owner_execution


def step(title):
    print(f"\n-- {title} " + "-" * max(0, 60 - len(title)))


def stop(message):
    print(f"FAILED: {message}")
    sys.exit(1)


def say(text, fraction=None):
    print(f"  {text}", flush=True)


# ── 1. WSL ───────────────────────────────────────────────────────────────
step("WSL")
proc = linux.wsl("--version")
if proc.returncode != 0:
    stop(f"{linux.WSL} doesn't answer: {(proc.stdout + proc.stderr).strip()[-500:]}\n"
         "Run setup.bat: it installs or updates WSL.")
print(f"Using: {linux.WSL}\n{proc.stdout.strip().splitlines()[0]}")

# ── 2. The tools ─────────────────────────────────────────────────────────
step("Cladia's shared Linux and tools (the first time: a few minutes)")
try:
    linux.ensure("tools", lambda text: print(text, end=""), say)
except Exception as e:
    stop(str(e))
print(f"OK: {linux.LINUX_DIR / linux.DISTROS['tools']['name']}")
workdir = Path(tempfile.mkdtemp(prefix="cladia-check-", dir=Path.home()))
for tool in (["mafft", "--version"], ["fasttreeMP", "-help"], ["iqtree3", "--version"],
             ["blastn", "-version"], ["minimap2", "--version"], ["samtools", "--version"],
             ["progressiveMauve", "--version"], ["primer3_core", "--about"]):
    code, out, err, _ = owner_execution.run_container(config.TOOLS_IMAGE, tool, workdir, "cladia-check")
    text = (out + err).strip()
    if tool[0] == "fasttreeMP" and "FastTree 2.2.0" not in text:
        stop(f"FastTree did not report version 2.2.0:\n{text[-1000:]}")
    if tool[0] == "minimap2" and (code != 0 or out.strip() != "2.31-r1302"):
        stop(f"minimap2 did not report version 2.31-r1302:\n{text[-1000:]}")
    expected = {"mafft": "v7.526", "iqtree3": "version 3.1.4", "blastn": "2.17.0+", "samtools": "samtools 1.25"}
    if tool[0] in expected and (code != 0 or expected[tool[0]] not in text):
        stop(f"{tool[0]} did not report the expected version:\n{text[-1000:]}")
    print(f"  {tool[0]:16s} -> {text.splitlines()[0] if text else f'exit {code}'}")

# ── 3. Engines' own images ───────────────────────────────────────────────
linux._load_engines()
for kind, distro in linux.DISTROS.items():
    if kind == "tools":
        continue
    step(f"{kind}'s isolated filesystem in Cladia ({distro['image']}; the first time: a few minutes)")
    try:
        linux.ensure(kind, lambda text: print(text, end=""), say)  # runs the engine's check
    except Exception as e:
        stop(str(e))
    print("OK")

# ── 4. MAFFT ─────────────────────────────────────────────────────────────
step("Folder mounting and MAFFT")
(workdir / "test.fasta").write_text(">a\nACGTACGTAAGGCTT\n>b\nACGTCGTAAGGCTT\n")
code, out, err, cmd = owner_execution.run_container(config.TOOLS_IMAGE, ["mafft", "--auto", "/data/test.fasta"], workdir, "cladia-check")
if code != 0 or not out.startswith(">"):
    stop(err.strip()[-2000:])
print("OK\n\n" + out)

shutil.rmtree(workdir, ignore_errors=True)
print("\nAll checks passed.")
