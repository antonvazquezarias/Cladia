"""The page's classic scripts share one global scope: a top-level name declared in
two of them is a SyntaxError that stops the later script loading entirely (a second
sizeText in app.js once left app-picker.js out, so the app opened broken)."""
import re
from collections import defaultdict
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "cladia" / "static"
DECLARATION = re.compile(r"^(?:async\s+)?(?:function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)


def test_no_top_level_name_declared_twice():
    scripts = re.findall(r'<script src="([^"]+)"', (STATIC / "index.html").read_text(encoding="utf-8"))
    where = defaultdict(list)
    for name in scripts:
        text = (STATIC / name).read_text(encoding="utf-8")
        if re.match(r"(?:\s*(?://[^\n]*|/\*.*?\*/))*\s*\(function", text, re.S):
            continue  # wrapped in its own function: its names stay inside
        for found in DECLARATION.findall(text):
            where[found].append(name)
    twice = {name: files for name, files in where.items() if len(files) > 1}
    assert not twice, f"declared in more than one script: {twice}"
