"""Diagnostic experiments only: no application fixes and no real library access.

Run with the app's Python: python scripts/probe_project_risks.py
Creates/removes a private temporary directory; workers use only CLADIA_DATA there.
Prints JSON measurements, including intentionally injected failures.
"""
import argparse
import ctypes
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cladia import config, history, library, projects
from cladia.genbank import records_text
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord


def memory():
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
    data = Counters()
    data.cb = ctypes.sizeof(data)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(data), data.cb):
        raise ctypes.WinError()
    return {"working_mib": round(data.WorkingSetSize / 2**20, 1), "peak_mib": round(data.PeakWorkingSetSize / 2**20, 1)}


def sample(name):
    return SeqRecord(Seq("ACGT" * 250), id=name, name=name, description="diagnostic", annotations={"molecule_type": "DNA"})


def setup():
    library.ensure_folders()
    return library.add_list([sample("first"), sample("second")], "samples")


def worker(mode, archive):
    library.ensure_folders()
    if mode == "crash":
        library._save = lambda *a, **kw: os._exit(77)
        projects.import_zip(archive, "Interrupted")
    if mode == "restart":
        from cladia.__main__ import free_port, start_server
        start_server(free_port())
        return {"integrity": library.check_integrity(),
                "staging_dirs": len(list(projects.transfer_root().glob("*"))),
                "recovery": json.loads((config.APP_DIR / "transfer-recovery.json").read_text())}
    library.add_folder("Concurrent target")
    baseline = memory()
    event, errors = threading.Event(), []
    if mode == "export":
        original = projects._hash
        def observe(path):
            event.set()
            return original(path)
        projects._hash = observe
        operation = lambda: projects.export_zip(archive)
    else:
        original = projects.validate_zip
        def observe(*a, **kw):
            event.set()
            return original(*a, **kw)
        projects.validate_zip = observe
        operation = lambda: projects.import_zip(archive)
    start = time.perf_counter()
    def run():
        try: operation()
        except BaseException as exc: errors.append(repr(exc))
    thread = threading.Thread(target=run)
    thread.start()
    if not event.wait(30):
        raise RuntimeError("operation did not start: " + repr(errors))
    read_start = time.perf_counter()
    library.items()
    read_seconds = time.perf_counter() - read_start
    write_start = time.perf_counter()
    library.rename_folder("Concurrent target", "Concurrent write")
    write_seconds = time.perf_counter() - write_start
    thread.join()
    return {"mode": mode, "duration_seconds": round(time.perf_counter() - start, 3),
            "read_seconds": round(read_seconds, 3), "write_wait_seconds": round(write_seconds, 3),
            "baseline": baseline, "after": memory(), "errors": errors}


def child(mode, data, archive):
    env = {**os.environ, "CLADIA_DATA": str(data)}
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", mode, "--archive", str(archive)],
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if mode == "crash":
        return {"exit_code": done.returncode}
    if done.returncode:
        raise RuntimeError(done.stdout + done.stderr)
    return json.loads(done.stdout.split("RESULT:")[-1])


def point_data(path):
    config.DATA_DIR = path
    config.APP_DIR = path / ".cladia"
    library.ensure_folders()


def metadata_failure(root, postwrite=False):
    point_data(root)
    one = library.add_sequence(sample("first"), "first")
    two = library.add_sequence(sample("second"), "second")
    original_save, original_checkpoint = library.save_record, history.checkpoint
    calls = 0
    if postwrite:
        def fail_after_write(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 3: raise OSError("injected history disk failure after file write")
            return original_checkpoint(*a, **kw)
        history.checkpoint = fail_after_write
    else:
        def fail_second(*a, **kw):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("injected second-file write failure")
            return original_save(*a, **kw)
        library.save_record = fail_second
    error, recovery = None, None
    try:
        library.import_table_metadata([one, two], [["first", "Alga"], ["second", "Seaweed"]], 0, {"host": 1}, apply=True)
    except ValueError as exc:
        error = str(exc)
    finally:
        library.save_record, history.checkpoint = original_save, original_checkpoint
    hosts = [library.record_metadata(library.get_item(i))["host"] for i in (one, two)]
    try:
        library.restore_version(library.get_item(one), 0)
        recovery = "restored"
    except (ValueError, OSError) as exc:
        recovery = str(exc)
    return {"error": error, "hosts_after_failure": hosts, "restore_previous": recovery,
            "host_after_restore": library.record_metadata(library.get_item(one))["host"]}


def folder_race(root, archive):
    point_data(root)
    setup()
    entered, release = threading.Event(), threading.Event()
    original = library._save
    errors = []
    def gated_save(*a, **kw):
        if threading.current_thread() is importer:
            entered.set()
            if not release.wait(10): raise RuntimeError("probe timed out")
        return original(*a, **kw)
    def run():
        try: projects.import_zip(archive)
        except BaseException as exc: errors.append(repr(exc))
    importer = threading.Thread(target=run)
    library._save = gated_save
    try:
        importer.start()
        if not entered.wait(10): raise RuntimeError("import did not reach index publication")
        created = "Created during import"
        creator = threading.Thread(target=lambda: library.add_folder(created))
        creator.start()
        release.set()
        importer.join(10)
        creator.join(10)
        return {"folder_created_successfully": created in library.folders(), "folder_survives_import": created in library.folders(), "errors": errors}
    finally:
        release.set()
        importer.join(10)
        library._save = original


def main():
    report = {}
    with tempfile.TemporaryDirectory(prefix="cladia-risk-probe-") as tmp:
        root = Path(tmp)
        point_data(root / "large")
        setup()
        # A kept BAM is opaque to ZIP transport. Random bytes exercise compression
        # and streaming costs; this is not a biological BAM-validity test.
        bam = library.library_dir() / "synthetic.bam"
        with bam.open("wb") as out:
            for i in range(256): out.write(os.urandom(2**20))
        Path(str(bam) + ".bai").write_bytes(b"diagnostic index")
        primary = library.library_dir() / "mapping.mapping.json"
        primary.write_text(json.dumps({"summary": {}}))
        library._register(primary, "mapping", {"bam": bam.name})
        archive = root / "large.zip"
        report["payload_mib"] = 256
        report["export"] = child("export", config.DATA_DIR, archive)
        report["zip_mib"] = round(archive.stat().st_size / 2**20, 1)
        report["import"] = child("import", root / "imported", archive)
        point_data(root / "history-source")
        owner = setup()
        # Valid GenBank list bytes, resembling an earlier much larger list.
        record = SeqRecord(Seq("ACGT" * 25000), id="old", name="old", annotations={"molecule_type": "DNA"})
        block = records_text([record], "genbank").encode()
        raw = block * ((128 * 2**20) // len(block))
        report["history_raw_mib"] = round(len(raw) / 2**20, 1)
        history.checkpoint_bytes(owner, raw)
        del raw, block, record
        gc.collect()
        history.checkpoint(owner, library.get_item(owner)["path"])
        histzip = root / "history.zip"
        projects.export_zip(histzip, include_history=True)
        report["history_zip_mib"] = round(histzip.stat().st_size / 2**20, 2)
        report["history_import"] = child("import", root / "history-import", histzip)
        report["concurrent_folder_creation"] = folder_race(root / "folder-race", histzip)
        # Forced exit after all new files are published but before items.json.
        point_data(root / "crash-target")
        survivor = library.add_sequence(sample("survivor"), "survivor")
        original = Path(library.get_item(survivor)["path"]).read_bytes()
        report["forced_crash"] = child("crash", config.DATA_DIR, histzip)
        report["after_restart"] = child("restart", config.DATA_DIR, histzip)
        report["existing_item_unchanged"] = Path(library.get_item(survivor)["path"]).read_bytes() == original
        report["metadata_second_file_failure"] = metadata_failure(root / "metadata")
        report["metadata_history_failure"] = metadata_failure(root / "metadata-history", postwrite=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=["export", "import", "crash", "restart"])
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.worker:
        print("RESULT:" + json.dumps(worker(args.worker, args.archive)))
    else:
        main()
