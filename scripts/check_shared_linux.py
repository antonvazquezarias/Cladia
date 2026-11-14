"""Real dependency-root, mount and cancellation checks; private scratch data only."""
import concurrent.futures
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cladia import config, linux
from cladia.tool_plugins import mfannot


def main():
    linux.ensure("tools", print)
    linux.ensure("mfannot", print)
    names = linux.registered()
    assert "Cladia" in names and not any(d["legacy"] in names for d in linux.DISTROS.values()), names
    token = uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="cladia-isolation-") as scratch:
        root = Path(scratch)
        for name in ("a", "b", "input"):
            (root / name).mkdir()
        (root / "input" / "value").write_text("unchanged")
        def run(image, folder, script, name):
            return linux.run(image, ["-c", script], root / folder, name,
                             mounts={str(root / "input"): "/input"}, entrypoint="bash")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(run, config.TOOLS_IMAGE, "a", "echo host > /data/value; sleep 1; cat /data/value; test ! -d /mfannot", "cladia-host-" + token)
            b = pool.submit(run, mfannot.IMAGE, "b", "echo engine > /data/value; sleep 1; cat /data/value; test -d /mfannot; ! touch /input/new", "cladia-engine-" + token)
            ra, rb = a.result(timeout=30), b.result(timeout=30)
            assert ra[0] == rb[0] == 0, (ra[:3], rb[:3])
            assert ra[1].strip() == "host" and rb[1].strip() == "engine"
        assert not (root / "input" / "new").exists()
        print("PASS: concurrent host/MFannot dependency roots and /data mounts stay separate; inputs remain read-only")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            name = "cladia-cancel-" + token
            cancelled = pool.submit(run, mfannot.IMAGE, "b", "touch /data/started; sleep 60", name)
            unaffected = pool.submit(run, config.TOOLS_IMAGE, "a", "sleep 3; echo survived", "cladia-other-" + token)
            deadline = time.monotonic() + 15
            while not (root / "b" / "started").exists():
                assert time.monotonic() < deadline, "Cancellation probe did not start"
                time.sleep(.05)
            linux.cancel(name)
            assert cancelled.result(timeout=10)[0] != 0
            other = unaffected.result(timeout=15)
            assert other[0] == 0 and other[1].strip() == "survived", other[:3]
        print("PASS: cancellation kills the selected job group without stopping another environment's job")
        volume = "cladia-check-" + token
        try:
            code, out, err, _ = linux.run(config.TOOLS_IMAGE, ["sh", "-c", "echo stored > /store/value"], root / "a",
                                          "cladia-volume-" + token, mounts={"volume:" + volume: "/store"})
            assert code == 0, err
            code, out, err, _ = linux.run(mfannot.IMAGE, ["sh", "-c", "cat /store/value"], root / "b",
                                          "cladia-read-" + token, mounts={"volume:" + volume: "/store"}, entrypoint="env")
            assert code == 0 and out.strip() == "stored", (out, err)
        finally:
            linux.remove_volume(volume)
        print("PASS: shared named volumes work across isolated roots and clean up successfully")
    print("All shared-runtime checks passed.")


if __name__ == "__main__":
    main()
