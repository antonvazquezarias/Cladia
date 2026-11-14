"""Process, container, image and volume execution services."""
import os
import re
import shlex
import subprocess
from pathlib import Path
from . import config, linux
from .job_progress import watch_log
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TOOLS_DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "containers" / "lab-tools"

def run_program(cmd, **kwargs):
    """subprocess.run for tools: no console window, output read as UTF-8.

    Without an explicit encoding Windows decodes with its old code page; the
    container program's UTF-8 progress output then fails to decode and the output comes back as None.
    """
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW, **kwargs)

def _try(cmd):
    """Runs a short check command; returns (ok, message)."""
    try:
        proc = run_program(cmd, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, (out[-1] if out else f"exit code {proc.returncode}")

def container_command():
    """The forced container program's command, e.g. ['wslc'] (None: Cladia's Linux)."""
    if not config.CONTAINER_CMD:
        return None
    return config.CONTAINER_CMD.split() if not os.path.exists(config.CONTAINER_CMD) else [config.CONTAINER_CMD]

def ensure_image(job, image):
    """What runs `image` is ready: Cladia's Linux made or updated the first time
    (a few minutes, once; the job's bar says so), or the container image built."""
    def log(text):
        job["log"] += text

    def report(text, fraction):
        if job.get("progress"):
            job["progress"]["detail"] = f"preparing Cladia's Linux (first time only): {text}"
            job["progress"]["fraction"] = fraction

    if not container_command():
        return linux.ensure(linux.kind_of(image), log, report)
    if image != config.TOOLS_IMAGE:
        return  # (pulled by the container program itself)
    exists, _ = _try([*container_command(), "image", "inspect", image])
    if exists:
        return
    log(f"Building {image} (first time only, a few minutes)...\n")
    proc = run_program([*container_command(), "build", "-t", image, str(TOOLS_DOCKERFILE_DIR)])
    if proc.returncode != 0:
        raise RuntimeError(f"Building {image} failed:\n{(proc.stdout + proc.stderr)[-2000:]}")

def ensure_tools_image(job):
    """The tools are ready (see ensure_image)."""
    ensure_image(job, config.TOOLS_IMAGE)

def run_container(image, args, workdir, name=None, mounts=None, entrypoint=None):
    """Runs `image args...` with workdir at /data. mounts: more host folders ->
    paths inside, read-only (e.g. read files), or "volume:<name>" -> path, the
    job's own writable space on the Linux side; entrypoint: instead of the image's
    own. Returns (exit code, stdout, stderr, cmd)."""
    if not container_command():
        return linux.run(image, args, workdir, name or "cladia-job", mounts, entrypoint)
    named = ["--name", name] if name else []
    named += ["--entrypoint", entrypoint] if entrypoint else []
    extra = [a for host, inside in (mounts or {}).items()
             for a in ("-v", f"{host[7:]}:{inside}" if host.startswith("volume:") else f"{host}:{inside}:ro")]
    cmd = [*container_command(), "run", "--rm", *named, "-v", f"{Path(workdir).resolve()}:/data", *extra, image, *args]
    proc = run_program(cmd)
    return proc.returncode, proc.stdout, proc.stderr, cmd

def volume_create(name):
    """A job's own space on the Linux side (made when first mounted in Cladia's Linux)."""
    _volume_name(name)
    if not container_command():
        return True, ""
    return _try([*container_command(), "volume", "create", name])

def volume_remove(name):
    _volume_name(name)
    if not container_command():
        return linux.remove_volume(name)
    ok, message = _try([*container_command(), "volume", "remove", name])
    if not ok and "no such volume" not in message.lower():
        raise RuntimeError(f"Could not remove tool volume {name}: {message}")


def _volume_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Invalid tool volume name")

def tool(job, image, args, workdir, mounts=None, entrypoint=None):
    """Runs a container, appends its output to the job log, raises on failure."""
    if job.get("cancel"):
        raise RuntimeError("cancelled")
    ensure_image(job, image)
    job.setdefault("_run_details", {}).setdefault("commands", []).append(shlex.join(([entrypoint] if entrypoint else []) + args))
    if job.get('kind') == 'tool-install' and not container_command():
        def output(line):
            job['log'] += line
            if job.get('progress') and line.strip():
                job['progress'].update(detail=line.strip()[:180], fraction=None)
        if job.get('progress'):
            job['progress'].update(detail='Downloading and installing tool packages', fraction=None)
        code, out, err, cmd = linux.run(image, args, workdir, name=f"cladia-{job['id']}",
                                         mounts=mounts, entrypoint=entrypoint, on_output=output)
        if code != 0:
            raise RuntimeError(f"{args[0]} exited with code {code}:\n{err.strip()[-2000:]}")
        return out
    code, out, err, cmd = run_container(image, args, workdir, name=f"cladia-{job['id']}", mounts=mounts,
                                        entrypoint=entrypoint)
    job["log"] += "$ " + " ".join(cmd) + "\n" + err
    if code != 0:
        raise RuntimeError(f"{args[0]} exited with code {code}:\n{err.strip()[-2000:]}")
    return out

def tool_watched(job, args, workdir, watch, stdout=False):
    """tool with the program's messages (stderr; stdout=True: both, for a program
    that writes its results to files and talks on stdout) going to run.log as they
    come, so the Jobs bar can follow them using the supplied progress reader."""
    log = workdir / "run.log"
    watch_log(job, log, watch)
    redirect = ">/data/run.log 2>&1" if stdout else "2>/data/run.log"
    try:
        return tool(job, config.TOOLS_IMAGE, ["bash", "-c", f"{shlex.join(args)} {redirect}"], workdir)
    except RuntimeError as e:
        tail = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
        raise RuntimeError(f"{e}\n{tail}".strip()) from None

def set_option(args, flag, value):
    """Sets `flag value` in an option list, replacing the strategy's own value."""
    if flag in args:
        args[args.index(flag) + 1] = str(value)
    else:
        args += [flag, str(value)]

def in_parallel(job, image, workdir, commands, labels):
    """Runs shell commands (one per sequence) in one container, job["cores"] of them
    at a time. Each one's output goes into the job's log under its label; run.log
    gets "finished <i> <exit code>" lines, which the Jobs bar counts
    (parse_parallel). Returns {i: exit code}."""
    tasks, logs = workdir / "tasks", workdir / "logs"
    tasks.mkdir()
    logs.mkdir()
    for i, command in enumerate(commands):
        (tasks / f"{i}.sh").write_text(command + "\n", newline="\n")
    (workdir / "run.log").write_text("")
    script = ("ls /data/tasks | sed 's/.sh$//' | xargs -P " + str(max(1, job.get("cores") or 1)) + " -I{} "
              "sh -c 'bash /data/tasks/{}.sh > /data/logs/{}.log 2>&1; echo \"finished {} $?\" >> /data/run.log'")
    job["progress"]["total"] = len(commands)
    watch_log(job, workdir / "run.log", parse_parallel)
    tool(job, image, ["-c", script], workdir, entrypoint="bash")
    job.pop("live_log")  # (the commands' own logs go in below)
    job.pop("watch")
    codes = {int(i): int(c) for i, c in re.findall(r"^finished (\d+) (\d+)", (workdir / "run.log").read_text(), re.M)}
    for i, label in enumerate(labels):
        text = (logs / f"{i}.log").read_text(encoding="utf-8", errors="replace") if (logs / f"{i}.log").exists() else ""
        if text.strip() or codes.get(i):
            job["log"] += f"\n── {label}{'' if not codes.get(i) else f' (exit code {codes[i]})'} ──\n{text[-20000:]}"
    return codes

def parse_parallel(text, progress):
    """in_parallel's run.log: a line "finished <i> <exit code>" per sequence."""
    total = progress.get("total") or 1
    done = len(re.findall(r"^finished ", text, re.M))
    if total == 1:
        return None  # one sequence: a shimmer until it's done
    return {"at": progress["at"], "fraction": done / total, "detail": f"{done} of {total} sequences"}
