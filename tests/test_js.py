"""The JavaScript tests (tests/test_*.js, plain node scripts): run here when Node is
there (on the PATH, or unpacked at %LOCALAPPDATA%\\node), skipped when it isn't."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCAL = Path(os.environ.get("LOCALAPPDATA", "")) / "node" / "node.exe"
NODE = shutil.which("node") or (str(LOCAL) if LOCAL.is_file() else None)


@pytest.mark.skipif(not NODE, reason="Node isn't installed")
@pytest.mark.parametrize("script", sorted(p.name for p in (ROOT / "tests").glob("test_*.js")))
def test_js(script):
    done = subprocess.run([NODE, f"tests/{script}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
