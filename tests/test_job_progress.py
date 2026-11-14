"""Polling avoids reparsing unchanged logs and invalidates on engine/host changes."""
from pathlib import Path
from unittest.mock import Mock
from cladia import job_progress


def test_live_progress_cache_tracks_log_and_host_changes(tmp_path, monkeypatch):
    log = tmp_path / "live.log"
    log.write_text("first\npartial")
    reader = Mock(side_effect=lambda text, progress: {"at": progress["at"], "detail": text})
    job = {}
    job_progress.steps(job, "First", "Second")
    job_progress.watch_log(job, log, reader)
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert job_progress.current_progress(job)["detail"] == "first\n"
    assert job_progress.current_progress(job)["detail"] == "first\n"
    assert len(reads) == reader.call_count == 1
    log.write_text("first\npartial line\n")
    assert job_progress.current_progress(job)["detail"] == "first\npartial line\n"
    job_progress.step(job, 1)
    assert job_progress.current_progress(job)["at"] == 1
    job_progress.watch_log(job, log, reader, len("first\n"))
    assert job_progress.current_progress(job)["detail"] == "partial line\n"
    log.write_text("new\n")
    job_progress.watch_log(job, log, reader)
    assert job_progress.current_progress(job)["detail"] == "new\n"
    assert len(reads) == reader.call_count == 5
    job["watch"] = None
    assert job_progress.current_progress(job) == job["progress"]
    assert reader.call_count == 5


def test_missing_or_removed_log_does_not_break_polling(tmp_path, monkeypatch):
    log = tmp_path / "live.log"
    job = {}
    reader = Mock()
    job_progress.steps(job, "Running")
    job_progress.watch_log(job, log, reader)
    assert job_progress.current_progress(job) == job["progress"]
    log.write_text("running\n")

    def removed(*args, **kwargs):
        raise FileNotFoundError("Job removed its log between stat and read")

    monkeypatch.setattr(Path, "read_text", removed)
    assert job_progress.current_progress(job) == job["progress"]
    reader.assert_not_called()
