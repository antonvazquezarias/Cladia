"""Help: the user and installation guides as web pages, and where the log files are.

The guides are the Markdown files in docs/, which come with every copy of the
program; they are shown in the default browser from Cladia's own server, so they
always match the version installed.
"""

import html
import os
import re
from pathlib import Path

from . import config, reveal

DOCS = Path(__file__).resolve().parent.parent / "docs"
INSTALLATION = "INSTALLATION.md" if os.name == "nt" else "INSTALLATION_LINUX.md"  # (this PC's)
PAGES = {"guide": ("USER_GUIDE.md", "Cladia user guide"),
         "installation": (INSTALLATION, "Installing Cladia")}
LINKS = {"USER_GUIDE.md": "/guide", INSTALLATION: "/guide/installation"}

STYLE = """
body { font: 16px/1.55 "Segoe UI", system-ui, sans-serif; color: #1f2933; background: #fff;
       max-width: 52em; margin: 0 auto; padding: 24px 28px 80px; }
h1, h2, h3 { line-height: 1.25; } h2 { margin-top: 2em; padding-top: .6em; border-top: 1px solid #d9dee4; }
a { color: #1d6fa5; } code { background: #f0f2f5; padding: 1px 4px; border-radius: 3px; font-size: .92em; }
table { border-collapse: collapse; } th, td { border: 1px solid #d9dee4; padding: 4px 10px; text-align: left; }
li { margin: .25em 0; }
"""


def page(name):
    """The guide as an HTML page; None if there is no such page or file."""
    import markdown  # imported here: only needed when someone opens the guide
    if name not in PAGES:
        return None
    file, title = PAGES[name]
    try:
        text = (DOCS / file).read_text(encoding="utf-8")
    except OSError:
        return None
    body = markdown.markdown(text, extensions=["tables", "toc", "sane_lists"], tab_length=2)  # the guides nest lists by two spaces
    # links between the two guides stay inside Cladia; other project files aren't served
    body = re.sub(r'href="([^"#]+\.md)(#[^"]*)?"',
                  lambda m: f'href="{LINKS[m.group(1)]}{m.group(2) or ""}"' if m.group(1) in LINKS else 'href="#"', body)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{STYLE}</style></head><body>{body}</body></html>")


def show_logs():
    """Opens Cladia's own folder (cladia.log, job logs in logs) in Explorer."""
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    reveal.show(config.APP_DIR)
    return str(config.APP_DIR)
