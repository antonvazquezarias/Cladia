"""Settings. Each can be overridden with an environment variable. The user's own
settings (the Settings dialog) are in .cladia/settings.json: setting()."""

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

# Where sequence files live (one folder per PC). An existing SeqLab library is
# selected until the app has safely renamed it on startup.
DEFAULT_DATA_DIR = Path.home() / "Cladia"
LEGACY_DATA_DIR = Path.home() / "SeqLab"
_override = os.environ.get("CLADIA_DATA")
DATA_DIR = Path(_override) if _override else (
    LEGACY_DATA_DIR if LEGACY_DATA_DIR.exists() and not (DEFAULT_DATA_DIR / "library").exists()
    else DEFAULT_DATA_DIR)

# Folder for job workspaces and other app files, inside the data folder
APP_DIR = DATA_DIR / (".seqlab" if DATA_DIR == LEGACY_DATA_DIR or
                      (DATA_DIR == DEFAULT_DATA_DIR and (DATA_DIR / ".seqlab").exists() and
                       not (DATA_DIR / ".cladia").exists()) else ".cladia")


def migrate_default_data():
    """Move the old library intact on first Cladia launch, after its instance lock."""
    global DATA_DIR, APP_DIR
    if _override:
        return False
    if DATA_DIR == DEFAULT_DATA_DIR:
        old_settings = DATA_DIR / ".seqlab"
        new_settings = DATA_DIR / ".cladia"
        if old_settings.exists() and not new_settings.exists():
            old_settings.rename(new_settings)
            APP_DIR = new_settings
            return True
        return False
    if DATA_DIR != LEGACY_DATA_DIR or DEFAULT_DATA_DIR.exists():
        return False
    LEGACY_DATA_DIR.rename(DEFAULT_DATA_DIR)
    DATA_DIR = DEFAULT_DATA_DIR
    old_settings = DEFAULT_DATA_DIR / ".seqlab"
    new_settings = DEFAULT_DATA_DIR / ".cladia"
    if old_settings.exists() and not new_settings.exists():
        try:
            old_settings.rename(new_settings)
        except OSError:
            APP_DIR = old_settings
            return True  # library remains accessible; retry settings migration later
    APP_DIR = new_settings if new_settings.exists() or not old_settings.exists() else old_settings
    return True

# Jobs run in Cladia's own Linux (linux.py: one WSL distribution with isolated tool roots).
# CONTAINER_CMD (CLADIA_CONTAINER): a docker-style program instead (`<cmd> run --rm
# -v ... image args`), e.g. wslc, or the tests' stand-in script.
CONTAINER_CMD = os.environ.get("CLADIA_CONTAINER", "")

# The tools' version: a new one runs containers/lab-tools/setup.sh again in Cladia's tools environment
TOOLS_VERSION = "0.8"  # MAFFT 7.526, IQ-TREE 3.1.4, BLAST+ 2.17.0, samtools/HTSlib 1.25
# The tools' image name; engines with an image of their own declare it (ENGINE["image"])
# and linux.kind_of maps image names to isolated environments
TOOLS_IMAGE = os.environ.get("CLADIA_TOOLS_IMAGE", f"cladia-tools:{TOOLS_VERSION}")


# ── The user's settings (the Settings dialog) ───────────────────────────
TREE_LAYOUT_DEFAULTS = dict(align=False, support=True, lengths=False, ladderize=False)
LIBRARY_VIEW_DEFAULTS = {"folder": None, "expanded": []}
_SETTINGS_LOCK = threading.RLock()

SETTINGS = {
    "tree_layout": None,  # saved viewer choices; None permits migration from browser storage
    "library_view": None,  # folder navigation; independent of the local server's port
    "cores": 0,          # cores for computing jobs; 0 = all but one (jobs.budget)
    "text_size": 100,    # % : the windows' zoom (window.apply_text_size), for people who find it small
    "ncbi_email": "",    # sent with NCBI requests, as NCBI asks programs to
    "ncbi_api_key": "",  # NCBI E-utilities: 10 requests a second instead of 3 (not BLAST)
    "blast_databases": [],  # local BLAST databases (blastdb.py), kept by Cladia, not the dialog's fields
    "submitters": [],    # presets for GenBank submissions (submitter.py): contact, authors, reference
}


def settings():
    path = APP_DIR / "settings.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    result = {**deepcopy(SETTINGS), **{k: v for k, v in saved.items() if k in SETTINGS}}
    layout = result["tree_layout"]
    if layout is not None:
        result["tree_layout"] = {**TREE_LAYOUT_DEFAULTS, **{
            k: v for k, v in (layout.items() if isinstance(layout, dict) else [])
            if k in TREE_LAYOUT_DEFAULTS and type(v) is bool}}
    view = result["library_view"]
    if view is not None:
        view = view if isinstance(view, dict) else {}
        result["library_view"] = {
            "folder": view.get("folder") if isinstance(view.get("folder"), str) else None,
            "expanded": list(dict.fromkeys(p for p in view.get("expanded", []) if isinstance(p, str)))
            if isinstance(view.get("expanded", []), list) else [],
        }
    return result


def setting(name):
    return settings()[name]


def save_settings(changes):
    with _SETTINGS_LOCK:
        current = settings()
        new = {**current, **{k: v for k, v in changes.items() if k in SETTINGS}}
        if "tree_layout" in changes:
            new["tree_layout"] = {**TREE_LAYOUT_DEFAULTS, **(current["tree_layout"] or {}),
                                  **changes["tree_layout"]}
        if "library_view" in changes:
            new["library_view"] = {**LIBRARY_VIEW_DEFAULTS, **(current["library_view"] or {}),
                                   **changes["library_view"]}
        APP_DIR.mkdir(parents=True, exist_ok=True)
        temporary = APP_DIR / "settings.json.tmp"
        try:
            temporary.write_text(json.dumps(new, indent=1), encoding="utf-8")
            temporary.replace(APP_DIR / "settings.json")
        finally:
            temporary.unlink(missing_ok=True)
        return new
