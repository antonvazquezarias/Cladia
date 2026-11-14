"""Installing and uninstalling optional tools (Settings › Tools, and the Windows
installer's choices on first run). An optional engine's install downloads its tool
(conda.py for bioconda tools) and whatever else it needs; only when it succeeds is
the tool recorded as installed, and its tab appears in the tool dialogs."""
import shutil

from . import config, jobs, job_progress
from .item_inputs import InputError


def _lock(engine):
    if not engine.get("conda"):
        return None
    from .conda import lock_hash
    return lock_hash(engine["id"], engine["conda"]["version"])


def install_engines(choices):
    """choices: [{id, choices}]. Also updates an installed tool (its choices again)."""
    from .tool_registry import build_registry, save_installed_engine, invalidate_registry
    registry = build_registry()
    installs = []
    for choice in choices:
        engine = registry.engines.get(choice.get("id"))
        if not engine or not engine.get("optional"):
            raise InputError(400, "Unknown installable tool")
        allowed = {c["value"] for c in engine.get("installChoices", [])}
        selected = choice.get("choices", [])
        if not isinstance(selected, list) or set(selected) - allowed or allowed and not selected:
            raise InputError(400, f"Choose what to install for {engine['label']}")
        _refuse_if_busy(engine, "updated" if registry.available(engine) else "installed")
        installs.append((engine, selected))
    if not installs:
        raise InputError(400, "Choose a tool to install")

    def install_job(job, workdir):
        job_progress.steps(job, *(e["label"] for e, _ in installs))
        for n, (engine, selected) in enumerate(installs):
            job_progress.step(job, n, None, "Installing")
            engine["install"](job, workdir, selected)
            if job.get("cancel"):
                raise RuntimeError("cancelled")
            previous = registry.installed.get(engine["id"], [])
            save_installed_engine(engine["id"], [*previous, *selected], _lock(engine))
            invalidate_registry()
            job_progress.step(job, n, 1, "Ready")
    return jobs.submit("tool-install", "Install " + ", ".join(e["label"] for e, _ in installs),
                       install_job, resources=("download", 0))


def uninstall_engine(engine_id):
    """A job removing an installed optional tool (its downloaded files); results made with it stay."""
    from .tool_registry import build_registry, forget_installed_engine, invalidate_registry
    registry = build_registry()
    engine = registry.engines.get(engine_id)
    if not engine or not engine.get("optional") or not registry.available(engine):
        raise InputError(400, "That tool isn't installed")
    _refuse_if_busy(engine, "uninstalled")

    def uninstall_job(job, workdir):
        job_progress.steps(job, f"Removing {engine['label']}")
        engine["uninstall"](job, workdir)
        forget_installed_engine(engine_id)
        invalidate_registry()
    return jobs.submit("tool-uninstall", f"Uninstall {engine['label']}", uninstall_job, resources=("download", 0))


def _refuse_if_busy(engine, what):
    kind = engine.get("jobKind", engine["id"])
    if any(j["kind"] == kind and j["status"] in ("queued", "running") for j in jobs.JOBS.values()):
        raise InputError(409, f"{engine['label']} is running a job; it can be {what} when the job ends")


def retire_plugins():
    """Once, at startup: the plugin packages of Cladia versions before November 2026 are
    gone (their tools are now built in, downloaded from bioconda when installed).
    They are forgotten, not carried over: Settings › Tools offers them to install again."""
    old = config.APP_DIR / "plugins"
    if old.is_dir():
        shutil.rmtree(old, ignore_errors=True)
