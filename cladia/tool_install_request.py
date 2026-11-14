"""Record optional-tool choices from the Windows installer for the first app run."""
import json
import sys

from . import config

PENDING = "pending-tool-install.json"


def add(engine, choice=None):
    path = config.APP_DIR / PENDING
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        selected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        selected = {}
    selected.setdefault(engine, [])
    if choice is not None and choice not in selected[engine]:
        selected[engine].append(choice)
    path.write_text(json.dumps(selected, indent=2), encoding="utf-8")


def submit_pending():
    """The installer's choices as one install job (tools this Cladia no longer has are skipped)."""
    path = config.APP_DIR / PENDING
    if not path.exists():
        return None
    from .tool_install import install_engines
    from .tool_registry import build_registry
    selected = json.loads(path.read_text(encoding="utf-8"))
    known = build_registry().engines
    chosen = [{"id": engine, "choices": choices} for engine, choices in selected.items()
              if known.get(engine, {}).get("optional")]
    job = install_engines(chosen) if chosen else None
    path.unlink()
    return job


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("Usage: python -m cladia.tool_install_request ENGINE [PACKAGE]")
    add(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
