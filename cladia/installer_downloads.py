"""Run a selected optional tool to completion before Windows setup finishes."""
import argparse
import sys
import time

from . import config


def install(engine, choices, output=print, report=None):
    from .tool_install import install_engines
    from .job_progress import current_progress
    job = install_engines([{"id": engine, "choices": choices}])
    started = time.monotonic()
    offset, previous = 0, None
    while True:
        text = job.get("log", "")
        if len(text) > offset:
            output(text[offset:].rstrip())
            offset = len(text)
        progress = current_progress(job) or {}
        names = progress.get("steps", [])
        at = progress.get("at", 0)
        detail = f'{names[at] if at < len(names) else engine}: {progress.get("detail", "")}'
        if detail != previous:
            output(detail)
            previous = detail
        if report:
            fraction = progress.get('fraction')
            amount = int(1000 * (at + (fraction if fraction is not None else 0)) / max(1, len(names)))
            elapsed = int(time.monotonic() - started)
            report(-1 if fraction is None else min(999, max(0, amount)),
                   f'{detail} (elapsed {elapsed // 60}m {elapsed % 60:02d}s)')
        if job["_finished"].wait(0.5):
            # The event includes log saving and worker cleanup, not just download.
            output(job.get("log", "")[offset:].rstrip())
            if job["status"] != "done":
                output(job.get("error") or "Installation cancelled")
                return 1
            output("Ready to use.")
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine")
    parser.add_argument("choices", nargs="*")
    args = parser.parse_args(argv)
    from .__main__ import take_instance
    # Match app startup: tool environments are keyed by the library's final path.
    if not take_instance(config.LEGACY_DATA_DIR, "SeqLab") or not take_instance():
        print("Close Cladia and retry installing this tool.", flush=True)
        return 1
    original = config.DATA_DIR
    config.migrate_default_data()
    if config.DATA_DIR != original and not take_instance():
        print("Close Cladia and retry installing this tool.", flush=True)
        return 1
    try:
        return install(args.engine, args.choices, output=lambda text: print(text, flush=True),
                       report=lambda amount, detail: print(f'CLADIA_PROGRESS:{amount}|{detail}', flush=True))
    except Exception as exc:
        print(f"Could not install {args.engine}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
