"""Optional tools' installation choices flow from the engines into Windows setup."""
import json
import threading

import pytest

from cladia import config, tool_install_request
from cladia.tool_registry import build_registry
from scripts.installer_tools import render


@pytest.mark.parametrize('status, expected', [('done', 0), ('failed', 1), ('cancelled', 1)])
def test_setup_waits_for_tool_and_reports_outcome(monkeypatch, status, expected):
    from cladia import installer_downloads, tool_install
    finished = threading.Event()
    finished.set()
    job = {'_finished': finished, 'status': status, 'log': 'Downloaded packages',
           'error': 'Download failed' if status == 'failed' else None}
    calls, messages, updates = [], [], []
    monkeypatch.setattr(tool_install, 'install_engines', lambda selected: calls.append(selected) or job)
    assert installer_downloads.install('getorganelle', ['other_pt'], messages.append,
                                       lambda amount, detail: updates.append((amount, detail))) == expected
    assert updates[0][0] == -1  # Unknown progress requests an activity bar, not a fake percentage.
    assert calls == [[{'id': 'getorganelle', 'choices': ['other_pt']}]]
    assert 'Downloaded packages' in messages
    assert ('Ready to use.' in messages) == (status == 'done')


def test_installer_tasks_follow_the_optional_engines():
    tasks, runs = render(build_registry(installed={}).installable())
    for tool in ("getorganelle", "clustal_omega", "krait"):
        assert f'Name: "tool_{tool}"' in tasks
    assert 'Name: "tool_getorganelle\\other_pt"' in tasks
    assert "WizardIsComponentSelected('tool_getorganelle\\other_pt')" in runs
    assert "Arguments := Arguments + ' other_pt'" in runs
    assert runs.count("InstallOptionalTool(Arguments, 'GetOrganelle") == 1
    assert "alignment_snps" not in tasks  # built in: nothing to install
    assert 'Types: default custom; Flags: fixed' in tasks
    optional = [line for line in tasks.splitlines() if 'Name: "tool_' in line]
    assert all('Types: custom' in line for line in optional)
    assert 'Clustal Omega (about 34 MB)' in tasks
    assert 'GetOrganelle (about 1.4 GB)' in tasks
    assert 'extra space varies' in tasks
    clustal = next(line for line in optional if 'Name: "tool_clustal_omega"' in line)
    assert 'ExtraDiskSpaceRequired: 34000000' in clustal
    organelle = next(line for line in optional if 'Name: "tool_getorganelle"' in line)
    assert 'ExtraDiskSpaceRequired: 1400000000' in organelle
    database = next(line for line in optional if 'Name: "tool_getorganelle\\other_pt"' in line)
    assert 'ExtraDiskSpaceRequired' not in database
    extra_tasks, extra_runs = render([{"id": "example", "label": "Example", "choices": []}])
    assert 'Name: "tool_example"' in extra_tasks
    assert "Arguments := 'example'" in extra_runs
    assert "InstallOptionalTool(Arguments, 'Example" in extra_runs


def test_installer_can_queue_tool_without_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    tool_install_request.add("example")
    tool_install_request.add("getorganelle", "other_pt")
    tool_install_request.add("getorganelle", "other_pt")
    queued = json.loads((config.APP_DIR / tool_install_request.PENDING).read_text())
    assert queued == {"example": [], "getorganelle": ["other_pt"]}


def test_first_launch_queues_the_chosen_tools(tmp_path, monkeypatch):
    from cladia import tool_install
    monkeypatch.setattr(config, "APP_DIR", tmp_path / ".cladia")
    tool_install_request.add("getorganelle", "other_pt")
    tool_install_request.add("gone_tool")  # (a tool this Cladia no longer has is skipped)
    queued = []
    monkeypatch.setattr(tool_install, "install_engines", lambda choices: queued.append(choices) or {"id": "job"})
    assert tool_install_request.submit_pending() == {"id": "job"}
    assert queued == [[{"id": "getorganelle", "choices": ["other_pt"]}]]
    assert not (config.APP_DIR / tool_install_request.PENDING).exists()
