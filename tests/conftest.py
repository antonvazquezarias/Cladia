"""Keep the real user library outside the test suite's reach.

Individual tests can redirect config again, but their teardown must restore a
session scratch library, never the user's library (including for background jobs).
"""
import pytest
import threading
import time

EXAMPLE_IMAGE = "someone/example:1"


@pytest.fixture(scope="session", autouse=True)
def isolated_library_baseline(tmp_path_factory):
    from cladia import config, linux
    scratch = tmp_path_factory.mktemp("cladia-session")
    patch = pytest.MonkeyPatch()
    patch.setattr(config, "DATA_DIR", scratch)
    patch.setattr(config, "APP_DIR", scratch / ".cladia")
    patch.setattr(linux, "LINUX_DIR", scratch / "linux")
    patch.setenv("CLADIA_DATA", str(scratch))
    # A stand-in engine image, as an engine with ENGINE["image"] declares one
    linux.add_environment("example", {"name": EXAMPLE_IMAGE, "check": "true", "version": "1",
                                      "legacy": "SeqLab-Example"})
    # Keep the baseline until the pytest process exits: an escaped daemon thread
    # must not regain access to the real library during session teardown either.
    return scratch


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    # Run before fixture teardown restores monkeypatched paths. A job's status
    # can already say "done" while it is still publishing provenance and logs.
    from cladia import jobs
    deadline = time.monotonic() + 30
    while True:
        workers = [t for t in threading.enumerate()
                   if getattr(t, "_target", None) is jobs._run_then_next]
        if not workers:
            break
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if time.monotonic() >= deadline and any(t.is_alive() for t in workers):
            pytest.fail("A test left a background analysis job running for over 30 seconds")
    yield
