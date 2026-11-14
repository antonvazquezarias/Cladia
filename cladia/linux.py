"""One Cladia WSL distribution, with separate root filesystems for tool environments.

The general tools run in the distribution's root (optional tools from bioconda in their
own conda environments there, conda.py). An engine whose tool comes as a Docker image
declares it (``ENGINE["image"]``) and gets an independent filesystem made from that
image under /var/lib/cladia/environments/<engine id>. Jobs use private
mount namespaces and chroot, keeping each environment's libraries and package
manager separate. This is dependency isolation, not a sandbox for untrusted code.
Existing Cladia distributions are exported, checked, and retired only after their
replacement works. User distributions and the old shared wslc disk are untouched.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
import urllib.request
from pathlib import Path

from . import config

WSL = next((p for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WSL" / "wsl.exe",
                        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe") if p.exists()),
           Path("wsl.exe"))
LINUX_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Cladia" / "linux"
LEGACY_LINUX_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SeqLab" / "linux"
SETUP = Path(__file__).resolve().parent.parent / "containers" / "lab-tools" / "setup.sh"

DISTRO = "Cladia"
# The environments jobs run in: the shared tools root, then one per engine image
# (add_environment, called when the tool registry loads an engine with ENGINE["image"]).
DISTROS = {
    "tools": {"name": DISTRO, "legacy": "SeqLab", "root": "/",
              "image": "library/debian:bookworm-slim", "version": config.TOOLS_VERSION,
              "check": "command -v mafft && command -v minimap2 && command -v samtools"},
}
ENVIRONMENTS = "/var/lib/cladia/environments"
VOLUMES = "/var/lib/cladia/volumes"  # a job's own space on the Linux side (map_job's unpacked reads)

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_lock = threading.Lock()  # one setup at a time
_ready = set()            # distros checked this session


def wsl(*args, input=None, timeout=None):
    """wsl.exe with its own messages in UTF-8 (they are UTF-16 otherwise)."""
    return subprocess.run([str(WSL), *args], capture_output=True, input=input, timeout=timeout,
                          encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                          env={**os.environ, "WSL_UTF8": "1"})


def linux_path(path):
    """D:\\x\\y -> /mnt/d/x/y (WSL's own mount of Windows drives)."""
    p = Path(path).resolve()
    drive = p.drive.rstrip(":").lower()
    return f"/mnt/{drive}/" + "/".join(p.parts[1:])


def add_environment(kind, spec):
    """An engine's own image: spec {name: Docker Hub image, check: a shell command that
    succeeds once it works, version: change it to unpack the image again,
    legacy: an older SeqLab distribution it replaces (optional)}."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", kind) or kind == "tools":
        raise ValueError(f"Invalid environment name: {kind}")
    if not isinstance(spec, dict) or not all(isinstance(spec.get(k), str) and spec[k] for k in ("name", "check", "version")):
        raise ValueError("An engine image needs a name, a check command and a version")
    DISTROS[kind] = {"name": DISTRO, "legacy": spec.get("legacy"), "root": f"{ENVIRONMENTS}/{kind}",
                     "image": spec["name"].removeprefix("docker.io/"), "version": spec["version"],
                     "check": spec["check"]}


def _load_engines():
    """Engines declare their environments when the tool registry loads them (cached)."""
    from .tool_registry import build_registry
    build_registry()


def _known(kind):
    if kind not in DISTROS:
        _load_engines()
    return kind in DISTROS


def kind_of(image):
    """Which isolated environment runs an image name the jobs give."""
    image = image.removeprefix("docker.io/")
    if image != config.TOOLS_IMAGE:
        _load_engines()
    return next((kind for kind, d in DISTROS.items() if kind != "tools" and d["image"] == image), "tools")


def registered():
    proc = _checked(wsl("--list", "--quiet"), "Listing WSL distributions")
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _legacy_info(name):
    """What an older SeqLab installation recorded about a distribution, or None."""
    if name == "SeqLab":
        previous = _read(LEGACY_LINUX_DIR / "runtime.json")
        return previous.get("tools") if isinstance(previous, dict) else None
    return _read(LEGACY_LINUX_DIR / f"{name}.json")


def _legacy_name(kind, names):
    """An older SeqLab distribution to migrate. One whose metadata is gone (its
    folder removed by hand or by an uninstall) can't be migrated: it is left
    alone, never unregistered, and Cladia starts from scratch."""
    candidates = ("SeqLab", "SeqLab-tools") if kind == "tools" else (DISTROS[kind]["legacy"],)
    return next((name for name in candidates if name and name in names and _legacy_info(name) is not None), None)


@contextmanager
def _setup_lock():
    """Different Cladia data folders still share one machine-wide installation."""
    LINUX_DIR.mkdir(parents=True, exist_ok=True)
    with open(LINUX_DIR / "setup.lock", "a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (13, 36):
                        raise
                    time.sleep(.2)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
    temporary.replace(path)


def meta(kind):
    """Image metadata for one environment, independent of the WSL registration."""
    return _read(LINUX_DIR / "environments" / f"{kind}.json")


def _checked(proc, action):
    if proc.returncode:
        raise RuntimeError(f"{action}: {(proc.stdout + proc.stderr).strip()[-2000:]}")
    return proc


def _host(*args, **kwargs):
    return wsl("-d", DISTRO, "-u", "root", "--exec", *args, **kwargs)


def _owned(name):
    """Never adopt/unregister a same-named distro stored outside Cladia's directory."""
    if os.name != "nt":
        raise RuntimeError("WSL migration requires Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as parent:
        for i in range(winreg.QueryInfoKey(parent)[0]):
            with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as key:
                if winreg.QueryValueEx(key, "DistributionName")[0] == name:
                    base = winreg.QueryValueEx(key, "BasePath")[0]
                    if str(base).startswith("\\\\?\\"):
                        base = str(base)[4:]
                    root = LEGACY_LINUX_DIR if name.startswith("SeqLab") else LINUX_DIR
                    if Path(base).resolve() == (root / name).resolve():
                        return
                    break
    raise RuntimeError(f"{name} is not stored in Cladia's managed Linux directory; leaving it untouched")


def _legacy_idle(name):
    proc = _checked(wsl("--list", "--running", "--quiet"), "Checking running Linux environments")
    if name in {line.strip() for line in proc.stdout.splitlines()}:
        raise RuntimeError(f"Close jobs and terminals using {name} before Cladia migrates it")


def _export(name, report):
    _owned(name)
    _legacy_idle(name)
    target = LINUX_DIR / "download" / f"{name}.tar"
    target.parent.mkdir(parents=True, exist_ok=True)
    report(f"moving the existing {name} installation", None)
    partial = target.with_suffix(".partial.tar")
    partial.unlink(missing_ok=True)
    _checked(wsl("--export", name, str(partial)), f"Exporting {name}")
    partial.replace(target)
    return target


# ── Images from Docker Hub ───────────────────────────────────────────────

MANIFESTS = ",".join(["application/vnd.oci.image.index.v1+json",
                      "application/vnd.docker.distribution.manifest.list.v2+json",
                      "application/vnd.oci.image.manifest.v1+json",
                      "application/vnd.docker.distribution.manifest.v2+json"])


def _get(url, token, accept=None):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    if accept:
        request.add_header("Accept", accept)
    return urllib.request.urlopen(request, timeout=60)


def fetch_image(image, folder, report=lambda text, fraction: None):
    """Downloads an image's layers (linux/amd64) into folder: returns (layer files
    in order, the image's config). report(text, fraction) follows the download."""
    if "@" in image:  # pinned: name@sha256:...
        repo, _, tag = image.partition("@")
    else:
        repo, _, tag = image.partition(":")
    repo = repo if "/" in repo else f"library/{repo}"
    token = json.load(urllib.request.urlopen(
        f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull", timeout=60))["token"]
    base = f"https://registry-1.docker.io/v2/{repo}"
    manifest = json.load(_get(f"{base}/manifests/{tag or 'latest'}", token, MANIFESTS))
    if "manifests" in manifest:  # a list for several platforms: this PC's
        entry = next(m for m in manifest["manifests"]
                     if m.get("platform", {}).get("os") == "linux" and m["platform"].get("architecture") == "amd64")
        manifest = json.load(_get(f"{base}/manifests/{entry['digest']}", token, entry["mediaType"]))
    image_config = json.load(_get(f"{base}/blobs/{manifest['config']['digest']}", token))
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(layer["size"] for layer in manifest["layers"]) or 1
    done, files = 0, []
    for i, layer in enumerate(manifest["layers"]):
        path = folder / f"layer{i:02d}.tar.gz"
        if not (path.exists() and path.stat().st_size == layer["size"]):  # (a download cut short is fetched again)
            with _get(f"{base}/blobs/{layer['digest']}", token) as response, open(path, "wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    report(f"downloading {(done + out.tell()) / 1e6:.0f} of {total / 1e6:.0f} MB",
                           (done + out.tell()) / total)
        done += layer["size"]
        files.append(path)
    return files, image_config.get("config", {})


# Unpacks one more layer over the root: first what it deletes (whiteouts: .wh.<name>
# removes <name>, .wh..wh..opq empties its folder), then its files
APPLY_LAYER = r'''set -e
root=$2
tar -tzf "$1" | while IFS= read -r p; do
  b=$(basename "$p"); d=$(dirname "$p")
  case "$b" in
    .wh..wh..opq) if [ -d "$root/$d" ]; then find "$root/$d" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; fi ;;
    .wh.*) rm -rf "$root/$d/${b#.wh.}" ;;
  esac
done
tar -xzpf "$1" -C "$root" --exclude='.wh.*' --numeric-owner
'''


WSL_CONF = "[interop]\nappendWindowsPath = false\n[automount]\noptions = \"metadata\"\n"


def _image_info(image_config):
    return {"version": None, "env": image_config.get("Env") or [], "workdir": image_config.get("WorkingDir") or "/",
            "entrypoint": image_config.get("Entrypoint") or [], "cmd": image_config.get("Cmd") or []}


def _adopt_shared_contents():
    """Carry the old shared distro's environments and databases into Cladia."""
    script = r'''set -e
mkdir -p /var/lib/cladia/environments /var/lib/cladia/volumes
for old in /var/lib/seqlab/environments/*; do
  [ -d "$old" ] || continue
  kind=$(basename "$old")
  new=/var/lib/cladia/environments/$kind
  [ ! -e "$new" ] || { echo "Both old and new $kind environments exist" >&2; exit 1; }
  mv "$old" "$new"
  if [ -e "$new/.seqlab-image.json" ] && [ ! -e "$new/.cladia-image.json" ]; then
    mv "$new/.seqlab-image.json" "$new/.cladia-image.json"
  fi
done
for item in /var/lib/seqlab/volumes/*; do
  [ -e "$item" ] || continue
  target=/var/lib/cladia/volumes/$(basename "$item")
  [ ! -e "$target" ] || { echo "Both old and new tool volumes exist" >&2; exit 1; }
  mv "$item" "$target"
done
'''
    _checked(_host("sh", "-c", script), "Migrating Cladia's existing tool data")
    for kind in [k for k in DISTROS if k != "tools"]:
        previous = _read(LEGACY_LINUX_DIR / "environments" / f"{kind}.json")
        if previous and meta(kind) is None:
            _save(LINUX_DIR / "environments" / f"{kind}.json", previous)


def _ensure_runtime(job_log, report):
    names = registered()
    marker = LINUX_DIR / "runtime.json"
    state = _read(marker)
    if DISTRO in names:
        _owned(DISTRO)
        if not state:
            raise RuntimeError("A Cladia WSL distribution already exists without migration metadata; leaving it untouched")
        if state.get("ready"):
            return
    else:
        legacy = _legacy_name("tools", names)
        info = _legacy_info(legacy) if legacy else None
        if info:
            archive = _export(legacy, report)
        else:
            layers, image = fetch_image(DISTROS["tools"]["image"], LINUX_DIR / "download" / "runtime", report)
            if len(layers) != 1:
                raise RuntimeError("The shared runtime base must have one root filesystem layer")
            archive, info = layers[0], _image_info(image)
        target = LINUX_DIR / DISTRO
        target.mkdir(parents=True, exist_ok=True)
        state = {"ready": False, "source": legacy, "tools": info}
        _save(marker, state)
        _checked(wsl("--import", DISTRO, str(target), str(archive), "--version", "2"), "Creating Cladia's shared Linux")
        _save(LINUX_DIR / "environments" / "tools.json", info)
    if state.get("source") == "SeqLab":
        _adopt_shared_contents()
    if meta("tools") is None and state.get("tools"):
        _save(LINUX_DIR / "environments" / "tools.json", state["tools"])
    _checked(_host("sh", "-c", "command -v unshare >/dev/null && command -v chroot >/dev/null && command -v mount >/dev/null || "
                   "(apt-get update && apt-get install -y --no-install-recommends util-linux mount ca-certificates)"),
             "Preparing the shared runtime")
    _checked(_host("sh", "-c", f"printf %s {shlex.quote(WSL_CONF)} > /etc/wsl.conf"), "Configuring Cladia's Linux")
    _checked(wsl("--terminate", DISTRO), "Restarting Cladia's Linux configuration")
    state["ready"] = True
    _save(marker, state)
    job_log("Cladia's shared Linux is ready.\n")


def _make_environment(kind, job_log, report):
    d = DISTROS[kind]
    legacy = d["legacy"]
    recovered = _host("cat", d["root"] + "/.cladia-image.json")
    if recovered.returncode == 0:
        info = json.loads(recovered.stdout)
        _save(LINUX_DIR / "environments" / f"{kind}.json", info)
        return info
    if _legacy_name(kind, registered()):
        info = _legacy_info(legacy)
        archive = _export(legacy, report)
        layers = None
    else:
        layers, image = fetch_image(d["image"], LINUX_DIR / "download" / kind, report)
        info = _image_info(image)
    root = shlex.quote(d["root"])
    # Build in a staging folder; a failed extraction never publishes a partial root.
    stage = shlex.quote(d["root"] + ".staging")
    _checked(_host("sh", "-c", f"rm -rf {stage} && mkdir -p {stage}"), f"Preparing {kind}")
    if layers is None:
        _checked(_host("tar", "-xpf", linux_path(archive), "-C", d["root"] + ".staging", "--numeric-owner"), f"Migrating {kind}")
    else:
        for i, layer in enumerate(layers):
            report(f"unpacking {kind} layer {i + 1} of {len(layers)}", i / len(layers))
            _checked(_host("sh", "-c", APPLY_LAYER, "sh", linux_path(layer), d["root"] + ".staging"), f"Unpacking {kind}")
    marker = shlex.quote(json.dumps(info))
    _checked(_host("sh", "-c", f"printf %s {marker} > {stage}/.cladia-image.json"), f"Saving {kind} metadata")
    _checked(_host("sh", "-c", f"test ! -e {root} && mv {stage} {root}"), f"Publishing {kind}")
    _save(LINUX_DIR / "environments" / f"{kind}.json", info)
    job_log(f"{kind}: independent filesystem ready inside {DISTRO}.\n")
    return info


def _environment_exec(kind, args, name="cladia-setup", on_output=None):
    command = _command(kind, args, name)
    options = dict(encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                   env={**os.environ, "WSL_UTF8": "1"})
    if on_output is None:
        return subprocess.run(command, capture_output=True, **options)
    return _stream_command(command, on_output)


def _stream_command(command, on_output):
    options = dict(encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                   env={**os.environ, "WSL_UTF8": "1"})
    # apt, source builds and curl can run for minutes. Drain both streams live
    # instead of hiding all their output until the entire bootstrap finishes.
    tail = ""
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          bufsize=1, **options) as process:
        for line in process.stdout:
            tail = (tail + line)[-12000:]
            on_output(line)
        code = process.wait()
    return subprocess.CompletedProcess(command, code, tail, "")


def _ensure_environment(kind, job_log, report):
    d = DISTROS[kind]
    info = meta(kind)
    if info is None:
        if kind == "tools":
            raise RuntimeError("Shared runtime has no tools metadata")
        info = _make_environment(kind, job_log, report)
    elif kind != "tools" and info.get("version") not in (None, d["version"]):
        # the engine names another image version: unpacked again from scratch
        job_log(f"{kind}: replacing version {info.get('version')} with {d['version']}.\n")
        _checked(_host("rm", "-rf", d["root"]), f"Removing the old {kind}")
        info = _make_environment(kind, job_log, report)
    if info.get("version") != d["version"]:
        if kind == "tools":
            report(f"installing {kind}", None)
            script = SETUP.read_text(encoding="utf-8").replace("\r\n", "\n")
            script += "\n" + (SETUP.parent / "upstream.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
            def output(line):
                job_log(line)
                if line.strip():
                    report(f"installing {kind}: {line.strip()[:180]}", None)
            proc = _environment_exec(kind, ["bash", "-c", script], on_output=output)
            _checked(proc, f"Installing {kind}")
            info["workdir"] = "/data"
        info["version"] = d["version"]
        _save(LINUX_DIR / "environments" / f"{kind}.json", info)
    _checked(_environment_exec(kind, ["sh", "-c", d["check"]]), f"Checking {kind}")
    _ready.add(kind)


def _retire_legacy(kind, job_log):
    legacy = _legacy_name(kind, registered())
    if not legacy:
        return
    _owned(legacy)
    _legacy_idle(legacy)
    # Older optional environments stored their databases in their own volume directory.
    if kind != "tools":
        root = shlex.quote(DISTROS[kind]["root"].replace("/var/lib/cladia/", "/var/lib/seqlab/") +
                           "/var/lib/seqlab/volumes")
        script = f'''set -e
mkdir -p {VOLUMES}
for v in {root}/*; do
  [ -e "$v" ] || continue
  dest={VOLUMES}/$(basename "$v")
  [ ! -e "$dest" ] || {{ echo "Volume already exists: $dest" >&2; exit 1; }}
  mv "$v" "$dest"
done
'''
        _checked(_host("sh", "-c", script), f"Preserving {kind} databases")
    _checked(wsl("--unregister", legacy), f"Removing migrated {legacy}")
    (LEGACY_LINUX_DIR / f"{legacy}.json").unlink(missing_ok=True)
    (LINUX_DIR / "download" / f"{legacy}.tar").unlink(missing_ok=True)
    job_log(f"{legacy}: verified and replaced by {DISTRO}.\n")


def _retire_old_optional_tools(job_log):
    """Once: what optional tools left before they came from bioconda (Nov 2026): GetOrganelle's
    own root filesystem and old database volumes, Clustal Omega's binary volumes."""
    marker = LINUX_DIR / "retired-optional-tools"
    if marker.exists():
        return
    script = (f"rm -rf /var/lib/cladia/environments/getorganelle /var/lib/cladia/environments/getorganelle.staging "
              f"{VOLUMES}/getorganelle-databases {VOLUMES}/cladia-clustalo-*; "
              f"for v in {VOLUMES}/cladia-getorganelle-*; do "
              f"case \"$(basename \"$v\")\" in cladia-getorganelle-databases-*) ;; *) rm -rf \"$v\" ;; esac; done; true")
    _checked(_host("sh", "-c", script, timeout=600), "Removing old optional tool files")
    (LINUX_DIR / "environments" / "getorganelle.json").unlink(missing_ok=True)
    marker.write_text("1", encoding="utf-8")
    job_log("Removed files left by earlier optional-tool installs (they now come from bioconda).\n")


def ensure(kind, job_log=lambda text: None, report=lambda text, fraction: None):
    """Prepare the shared runtime and the requested isolated environment.

    Existing installations migrate together on first use; replacements must all
    pass their checks before any old distribution is unregistered.
    """
    if not _known(kind):
        raise ValueError("Unknown Linux environment")
    def cached_runtime_exists():
        if kind not in _ready or "migration" not in _ready:
            return False
        if DISTRO in registered():
            return True
        # An uninstall or external WSL removal can invalidate this process's cache.
        _ready.clear()
        job_log("Cladia's Linux was removed; preparing it again.\n")
        return False

    if cached_runtime_exists():
        return
    with _lock, _setup_lock():
        if cached_runtime_exists():
            return
        _ensure_runtime(job_log, report)
        names = registered()
        legacy = [kind for kind in DISTROS if _legacy_name(kind, names)]
        for k in dict.fromkeys(["tools", *legacy, kind]):
            if k not in _ready:
                _ensure_environment(k, job_log, report)
        for k in legacy:
            _retire_legacy(k, job_log)
        _ready.add("migration")
        _retire_old_optional_tools(job_log)
        for folder in ("runtime", "getorganelle", *DISTROS):
            path = LINUX_DIR / "download" / folder
            if path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to((LINUX_DIR / "download").resolve()):
                shutil.rmtree(path)


# ── Running a job ────────────────────────────────────────────────────────

def _command(kind, program, name, binds=()):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Invalid Linux job name")
    root = DISTROS[kind]["root"]
    info = meta(kind) or {}
    lines = [f"echo $$ > /run/{name}.pid"]
    if root != "/":
        r = shlex.quote(root)
        lines += [f"mkdir -p {r}/dev {r}/proc {r}/sys {r}/data {r}/etc",
                  f"mount --rbind /dev {r}/dev", f"mount --make-rslave {r}/dev",
                  f"mount -t proc proc {r}/proc", f"mount --rbind /sys {r}/sys", f"mount --make-rslave {r}/sys",
                  f"rm -f {r}/etc/resolv.conf && cp -L /etc/resolv.conf {r}/etc/resolv.conf"]
    for source, target, readonly, create in binds:
        if not target.startswith("/") or ".." in target.split("/"):
            raise ValueError("Invalid Linux mount target")
        s = shlex.quote(source)
        t = shlex.quote((root.rstrip("/") + target))
        if create:
            lines.append(f"mkdir -p {s}")
        lines += [f'if [ -d {s} ]; then mkdir -p {t}; else mkdir -p "$(dirname {t})" && touch {t}; fi',
                  f"mount --bind {s} {t}"]
        if readonly:
            lines.append(f"mount -o remount,bind,ro {t}")
    env = ["HOME=/root", "LANG=C.UTF-8", *(info.get("env") or ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"])]
    inside = 'cd ' + shlex.quote(info.get("workdir") or "/data") + ' && exec env -i ' + ' '.join(shlex.quote(e) for e in env) + ' "$@"'
    if root == "/":
        lines.append('exec sh -c ' + shlex.quote(inside) + ' sh "$@"')
    else:
        lines.append('exec chroot ' + shlex.quote(root) + ' /bin/sh -c ' + shlex.quote(inside) + ' sh "$@"')
    script = "\n".join([*(f"{line} || exit 125" for line in lines[:-1]), lines[-1]])
    return [str(WSL), "-d", DISTRO, "-u", "root", "--exec", "setsid", "--wait", "unshare", "-m", "--propagation",
            "private", "sh", "-c", script, "sh", *program]


def command(image, args, workdir, name, mounts=None, entrypoint=None):
    """Build the job command in its own dependency root and mount namespace."""
    kind = kind_of(image)
    info = meta(kind) or {}
    binds = [(linux_path(workdir), "/data", False, False)]
    for host, target in (mounts or {}).items():
        if host.startswith("volume:"):
            volume = host[7:]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
                raise ValueError("Invalid Linux volume name")
            binds.append((f"{VOLUMES}/{volume}", target, False, True))
        else:
            binds.append((linux_path(host), target, True, False))
    program = ([entrypoint] if entrypoint else list(info.get("entrypoint") or [])) + list(args)
    return _command(kind, program, name, binds)


def run(image, args, workdir, name, mounts=None, entrypoint=None, on_output=None):
    """Runs a job's program; returns (exit code, stdout, stderr, cmd)."""
    cmd = command(image, args, workdir, name, mounts, entrypoint)
    if on_output is not None:
        proc = _stream_command(cmd, on_output)
        return proc.returncode, proc.stdout, proc.stdout, cmd
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW, env={**os.environ, "WSL_UTF8": "1"})
    return proc.returncode, proc.stdout, proc.stderr, cmd


def cancel(name):
    """Kill the job process group in the single shared distribution."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Invalid Linux job name")
    script = f'p=$(cat /run/{name}.pid 2>/dev/null) && kill -TERM -$p; sleep 1; [ -n "$p" ] && kill -KILL -$p 2>/dev/null; rm -f /run/{name}.pid; true'
    if DISTRO in registered():
        _host("sh", "-c", script, timeout=30)


def remove_volume(volume):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
        raise ValueError("Invalid Linux volume name")
    _checked(_host("rm", "-rf", f"{VOLUMES}/{volume}", timeout=120), f"Removing tool volume {volume}")


def sizes():
    """The shared virtual disk, plus any legacy disk still awaiting migration."""
    found = []
    _load_engines()
    legacy = [d["legacy"] for kind, d in DISTROS.items() if kind != "tools" and d["legacy"]]
    for name in [DISTRO, "SeqLab", "SeqLab-tools", "SeqLab-GetOrganelle", *legacy]:
        root = LEGACY_LINUX_DIR if name.startswith("SeqLab") else LINUX_DIR
        disk = root / name / "ext4.vhdx"
        if disk.exists():
            found.append({"name": name, "size": disk.stat().st_size, "used": name == DISTRO, "old": name != DISTRO})
    return found


def remove_all():
    """Remove Cladia's distribution; an older app's unmigrated data stays intact."""
    if DISTRO in registered():
        _owned(DISTRO)
        _checked(wsl("--unregister", DISTRO), f"Removing {DISTRO}")
    _ready.clear()
