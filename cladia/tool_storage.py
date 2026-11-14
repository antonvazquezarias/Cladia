"""Read-only tool footprints; shared runtime space is never charged to every tool."""
import json
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

from . import config, linux

KINDS = {"linux", "shared", "builtin", "remote"}
_PATH = re.compile(r"/[A-Za-z0-9_./*?+\-]*")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+\-]*")
_cache = {}
_lock = threading.Lock()


def validate(spec):
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        raise ValueError("Invalid tool storage kind")
    if "estimateBytes" in spec and (type(spec["estimateBytes"]) is not int or spec["estimateBytes"] < 0):
        raise ValueError("Invalid tool storage estimate")
    if spec["kind"] == "shared" and not _NAME.fullmatch(spec.get("uses", "")):
        raise ValueError("Invalid shared tool storage reference")
    if spec["kind"] == "linux":
        if spec.get("environment") not in linux.DISTROS:
            raise ValueError("Unknown tool storage environment")
        for key in ("paths", "packages", "volumes"):
            values = spec.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("Invalid tool storage paths")
            for value in values:
                if key == "paths":
                    if not _PATH.fullmatch(value) or ".." in value.split("/") or (value == "/" and spec["environment"] == "tools"):
                        raise ValueError("Unsafe tool storage path")
                elif not _NAME.fullmatch(value):
                    raise ValueError("Unsafe tool storage package or volume")


def _measure(specs):
    """One WSL call for all tools. Never installs or migrates environments."""
    try:
        if config.CONTAINER_CMD or linux.meta("tools") is None:
            return {}, None
        stamp = tuple((kind, (linux.LINUX_DIR / "environments" / f"{kind}.json").stat().st_mtime_ns)
                      for kind in linux.DISTROS if (linux.LINUX_DIR / "environments" / f"{kind}.json").exists())
    except (OSError, ValueError) as exc:
        return {}, str(exc)
    key = (str(linux.LINUX_DIR), stamp, json.dumps(specs, sort_keys=True))
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1], cached[2]
        lines = []
        for engine_id, spec in specs.items():
            validate(spec)
            if not _NAME.fullmatch(engine_id):
                raise ValueError("Invalid tool storage id")
            root = linux.DISTROS[spec["environment"]]["root"].rstrip("/")
            paths = [root + p for p in spec.get("paths", [])]
            paths += [linux.VOLUMES + "/" + v for v in spec.get("volumes", [])]
            lines.append("set --")
            if paths:
                # Paths are validated glob patterns, not arbitrary shell expressions.
                lines.append('for p in ' + ' '.join(paths) + '; do if [ -e "$p" ] || [ -L "$p" ]; then set -- "$@" "$p"; fi; done')
            if spec.get("packages"):
                packages = ' '.join(shlex.quote(p) for p in spec["packages"])
                lines.append(f'''for p in $(dpkg-query -L {packages} 2>/dev/null); do
  if [ -f "$p" ] || [ -L "$p" ]; then set -- "$@" "$p"; fi
done''')
            lines.append(f'''if [ "$#" -gt 0 ]; then
  amount=$(du -sb -c -- "$@" 2>/dev/null | tail -1 | cut -f1)
  printf '%s\\t%s\\n' {shlex.quote(engine_id)} "$amount"
fi''')
        try:
            proc = linux.wsl("-d", linux.DISTRO, "-u", "root", "--exec", "sh", "-c", "\n".join(lines), timeout=25)
            if proc.returncode:
                raise RuntimeError(proc.stderr.strip())
            measured = {}
            for line in proc.stdout.splitlines():
                name, sep, value = line.partition("\t")
                if sep and value.isdigit():
                    measured[name] = int(value)
            error = None
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            measured, error = {}, str(exc)
        _cache.clear()
        _cache[key] = (time.monotonic(), measured, error)
        return measured, error


def catalog(engines, measure=True):
    specs = {key: e["storage"] for key, e in engines.items() if e.get("storage", {}).get("kind") == "linux"}
    measured, error = _measure(specs) if measure else ({}, None)
    result = {}
    for key, engine in engines.items():
        spec = engine.get("storage", {})
        kind = spec.get("kind", "unknown")
        owner = spec.get("uses") if kind == "shared" else key
        owner_spec = engines.get(owner, {}).get("storage", spec)
        result[key] = {"kind": kind, "bytes": measured.get(owner), "estimateBytes": owner_spec.get("estimateBytes"),
                       "sharedWith": spec.get("sharedLabel", engines.get(owner, {}).get("label")) if kind == "shared" else None,
                       "note": spec.get("note", "Tool files; shared Linux dependencies are counted separately." if kind == "linux" else ""),
                       "measurementUnavailable": bool(error)}
    return result


def package_size(path):
    """Extracted package files, excluding separately installed dependencies."""
    if not Path(path).is_dir():
        return None
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except FileNotFoundError:
            pass  # an uninstall may finish while Settings measures
    return total
