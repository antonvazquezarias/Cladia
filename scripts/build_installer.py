"""Builds Cladia's installer (build\\Cladia-setup-<date>.exe) from the last commit:

    python scripts\\build_installer.py          (build\\Cladia-setup-<date>.exe)
    python scripts\\build_installer.py --lab    (build\\Cladia-lab-setup-<date>.exe)

--lab adds the files committed in a second repository whose git folder is .lab-git
in the project folder (engines kept out of this repository, e.g. a lab's own).
The committed code (git archive, so nothing half-done slips in) is staged in a
temporary folder, then Inno Setup compiles installer\\cladia.iss. Needs Inno Setup 6
(ISCC.exe; per user it goes to %LOCALAPPDATA%\\Programs).
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import io
import json
import time
from pathlib import Path

from installer_tools import render as render_optional_tools

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
STAGE = Path(tempfile.gettempdir()) / "cladia-installer-stage"  # outside OneDrive, which may hold files it syncs
if STAGE.resolve().parent != Path(tempfile.gettempdir()).resolve() or STAGE.name != "cladia-installer-stage":
    raise RuntimeError("Unexpected installer staging path")


def iscc():
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for path in (*local.glob("Programs/Inno*/ISCC.exe"), Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
                 Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")):
        if path.exists():
            return path
    sys.exit("Inno Setup 6 (ISCC.exe) not found: install it from jrsoftware.org")


LAB = "--lab" in sys.argv[1:]
if set(sys.argv[1:]) - {"--lab"}:
    sys.exit(__doc__)


def unpack(git):
    """The last commit of a repository (git = git's arguments) into the staging folder; its short id."""
    archive = subprocess.run([*git, "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(STAGE, filter="data")
    return subprocess.run([*git, "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True)
commit = unpack(["git"])
if LAB:
    if not (ROOT / ".lab-git").is_dir():
        sys.exit("--lab: no .lab-git repository in the project folder")
    commit += ", lab " + unpack(["git", "--git-dir", str(ROOT / ".lab-git")])
version = time.strftime("%Y.%m.%d")
name = f"Cladia-{'lab-' if LAB else ''}setup-{version}"
(STAGE / "VERSION").write_text(f"{version} ({commit})\n")
catalog = subprocess.run([sys.executable, "-c",
                          "import json; from cladia.tool_registry import build_registry; "
                          "print(json.dumps(build_registry(installed={}).installable()))"],
                         cwd=STAGE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                         capture_output=True, text=True, check=True)
tasks, runs = render_optional_tools(json.loads(catalog.stdout))
(STAGE / "optional-tool-tasks.iss").write_text(tasks, encoding="utf-8")
(STAGE / "optional-tool-runs.iss").write_text(runs, encoding="utf-8")
subprocess.run([str(iscc()), f"/DAppVersion={version}", f"/DStage={STAGE}", f"/DOutputName={name}",
                str(ROOT / "installer" / "cladia.iss")], check=True)
print(f"\n{BUILD / (name + '.exe')} (commit {commit})")
