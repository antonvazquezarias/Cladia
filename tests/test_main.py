"""The one-instance-per-data-folder guard."""

import os

import pytest

from cladia import config
from cladia.__main__ import take_instance


@pytest.mark.skipif(os.name != "nt", reason="the guard uses a Windows named mutex")
def test_one_instance_per_data_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "Cladia")
    assert take_instance()
    assert not take_instance()
