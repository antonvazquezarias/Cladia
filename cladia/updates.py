"""Is a newer installer waiting?

The installer leaves two files beside the program: VERSION (its date, YYYY.MM.DD,
written by scripts/build_installer.py) and UPDATES_FROM (the folder the installer
was run from, e.g. the lab's shared Ferramentas folder). A Cladia-setup-<later
date>.exe in that folder is an update. A copy run from the repository (run.bat)
has neither file and is never told about updates.
"""

import re
import subprocess
from pathlib import Path

PROGRAM = Path(__file__).resolve().parent.parent
INSTALLER = re.compile(r"(?:Cladia|Cladia-lab|SeqLab)-setup-(\d{4}\.\d{2}\.\d{2})\.exe", re.I)


def _read(name):
    try:
        return (PROGRAM / name).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def installed():
    """The installed version (YYYY.MM.DD), or None for a development copy."""
    found = re.match(r"\d{4}\.\d{2}\.\d{2}", _read("VERSION"))
    return found.group(0) if found else None


def newest(folder):
    """(version, path) of the newest installer in the folder, or None."""
    try:
        found = [(m.group(1), p) for p in Path(folder).iterdir() if (m := INSTALLER.fullmatch(p.name))]
    except OSError:  # the folder is gone, or not reachable just now
        return None
    return max(found, default=None)


def status():
    """{version, folder, newer: {version, path} | None}."""
    version, folder = installed(), _read("UPDATES_FROM") or None
    best = newest(folder) if version and folder else None
    newer = {"version": best[0], "path": str(best[1])} if best and best[0] > version else None
    return {"version": version, "folder": folder, "newer": newer}


def reveal():
    """The newer installer selected in Explorer (only that one: no path comes from the page)."""
    newer = status()["newer"]
    if not newer:
        raise ValueError("No newer installer was found")
    subprocess.Popen(f'explorer /select,"{newer["path"]}"')
