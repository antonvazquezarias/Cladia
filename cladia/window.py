"""What the app window can do that a normal browser can't: real file paths.

A web page never sees where a file lives on disk. The app window (pywebview)
can, for the Open and Save dialogs and for files dragged onto the window. In a
normal browser (development) files are uploaded and exports are downloads.
It also opens the extra view windows (New window); a browser opens a tab instead.
"""

import ctypes
import json
import os
import uuid
from pathlib import Path

WINDOWS = os.name == "nt"  # elsewhere (Linux: GTK and WebKit) the Windows-only parts are skipped
if WINDOWS:
    from ctypes import wintypes

import webview
from webview.dom import DOMEventHandler

FILE_TYPES = (
    # The format is read from the content (library.import_paths), so All files is the
    # default; the others only narrow the list (pywebview shows each one's endings).
    # Descriptions: letters, digits and spaces only (pywebview refuses the filter otherwise)
    "All files (*.*)",
    "Sequences (*.gb;*.gbk;*.gbff;*.genbank;*.embl;*.fasta;*.fa;*.fna;*.faa;*.fas;*.seq;*.txt;*.gz)",
    "Alignments (*.aln;*.fasta;*.fas;*.afa;*.nex;*.nexus;*.phy;*.phylip;*.sto)",
    "Trees (*.nwk;*.newick;*.tre;*.tree;*.treefile;*.contree;*.nex)",
    "Annotations GFF3 (*.gff3;*.gff)",
    "Sanger reads (*.ab1;*.abi;*.ab)",
)
EXPORT_TYPES = {
    "zip": ("Cladia project (*.zip)", "All files (*.*)"),
    "genbank": ("GenBank (*.gb)", "All files (*.*)"),
    "fasta": ("FASTA (*.fasta)", "All files (*.*)"),
    "gff3": ("GFF3 (*.gff3;*.gff)", "All files (*.*)"),
    "gff3fasta": ("GFF3 (*.gff3;*.gff)", "All files (*.*)"),
    "protein": ("FASTA (*.fasta;*.faa)", "All files (*.*)"),
    "newick": ("Newick (*.nwk;*.tre)", "All files (*.*)"),
    "nexus": ("NEXUS (*.nex;*.nexus)", "All files (*.*)"),
    "phylip": ("PHYLIP (*.phy;*.phylip)", "All files (*.*)"),
    "svg": ("SVG image (*.svg)", "All files (*.*)"),
    "pdf": ("PDF (*.pdf)", "All files (*.*)"),
    "png": ("PNG image (*.png)", "All files (*.*)"),
    "tiff": ("TIFF image (*.tif;*.tiff)", "All files (*.*)"),
    "bam": ("BAM (*.bam)", "All files (*.*)"),
    "gfa": ("GFA (*.gfa)", "All files (*.*)"),
    "vcf": ("VCF (*.vcf)", "All files (*.*)"),
    "csv": ("CSV (*.csv)", "All files (*.*)"),
    "tbl": ("Feature table (*.tbl)", "All files (*.*)"),
    "sbt": ("Submission template (*.sbt)", "All files (*.*)"),
}
NAMES = {"main"}  # the open Cladia windows' names (the w= in their address)
READ_TYPES = ("Reads (*.fastq.gz;*.fq.gz;*.fastq;*.fq;*.fasta;*.fa;*.fna;*.fasta.gz;*.fa.gz;*.fna.gz)", "All files (*.*)")
GFF_TYPES = ("Annotations and data tracks (*.gff3;*.gff;*.gtf;*.bed;*.bedgraph;*.bg;*.wig)", "All files (*.*)")
BLASTDB_TYPES = ("BLAST databases and FASTA (*.nal;*.nsq;*.ndb;*.pal;*.psq;*.pdb;*.fasta;*.fa;*.fna;*.faa;*.fas)",
                 "All files (*.*)")


class WindowApi:
    """Functions the page can call as window.pywebview.api.<name>()."""

    def __init__(self, url):
        self._url = url  # the local server's address

    def open_view(self, ids, title):
        """A second window showing only these items (app.js: view windows); its
        address names it (w=), for the unsaved-changes question when it closes."""
        name = uuid.uuid4().hex[:8]
        view = webview.create_window(f"Cladia · {title}", f"{self._url}?view={','.join(ids)}&w={name}",
                                     width=1200, height=850, min_size=(600, 400))
        NAMES.add(name)
        view.events.closing += lambda: ask_before_closing(view, {name}, "This window has")
        view.events.shown += lambda: (set_icon(view), follow_dpi(view))
        view.events.loaded += lambda: apply_text_size(view)
        view.events.closed += lambda: NAMES.discard(name)

    def pick_files(self):
        paths = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=FILE_TYPES
        )
        return list(paths or [])

    def pick_reads(self):
        """Open dialog for the Map reads tool: FASTQ files (their paths only, nothing is imported)."""
        paths = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True, file_types=READ_TYPES
        )
        return list(paths or [])

    def pick_project(self):
        paths = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN, file_types=EXPORT_TYPES["zip"])
        return (paths or [None])[0]

    def pick_annotations(self):
        """Open dialog for Import annotations: one GFF3 file's path."""
        paths = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=GFF_TYPES)
        return (paths or [None])[0]

    def pick_blastdb(self):
        """Open dialog for Settings > BLAST databases: a database's file, or FASTA."""
        paths = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN, file_types=BLASTDB_TYPES)
        return (paths or [None])[0]

    def save_file(self, filename, fmt):
        """Save dialog for Export; returns the chosen path, or None if cancelled."""
        path = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE, save_filename=filename, file_types=EXPORT_TYPES[fmt]
        )
        if isinstance(path, (list, tuple)):  # some pywebview versions return a tuple
            path = path[0] if path else None
        return path or None


# ── Screens with different scaling (Windows) ─────────────────────────────
# pywebview makes the program "system DPI aware": it knows only the main screen's
# scaling. On a screen with another one Windows stretches the window itself
# (blurry), and places it a few pixels off: maximized on a 100 % screen beside a
# 150 % one, it spilled a pixel onto the other screen and its top edge missed
# clicks (they reached the window behind; the user's report). Per-monitor
# awareness, declared before pywebview's call (which then does nothing), gives
# real pixels on each screen. WinForms doesn't resize a window that moves to a
# screen with other scaling, so follow_dpi does, as Windows suggests.

WM_DPICHANGED = 0x02E0
_subclasses = []  # the window procedures given to Windows: kept, or they'd be freed


def per_monitor_dpi():
    """Before the first window: real pixels on every screen (Windows 10 1703+)."""
    if not WINDOWS:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass  # an older Windows: pywebview's own setting
    # The taskbar shows Cladia as itself (its icon, pinned as Cladia), not as
    # pythonw.exe; the installer's shortcuts carry the same id.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Cladia.App")


ICON = Path(__file__).parent / "static" / "cladia.ico"


def set_icon(window):
    """Cladia's icon on a window's title bar and taskbar button (pywebview's
    WinForms windows otherwise take python's). Linux: the .desktop file's icon."""
    if not WINDOWS:
        return
    from System import Action
    from System.Drawing import Icon
    form = window.native
    form.Invoke(Action(lambda: setattr(form, "Icon", Icon(str(ICON)))))


def follow_dpi(window):
    """The window, moved to a screen with other scaling, takes the size Windows
    suggests (WM_DPICHANGED). Runs when the window is shown; hooked in on its own
    thread (a window can only be subclassed there)."""
    if not WINDOWS:
        return  # (GTK follows each screen's scaling itself)
    from System import Action  # .NET, through pywebview's pythonnet

    comctl32, user32 = ctypes.windll.comctl32, ctypes.windll.user32
    comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
    SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t)

    def proc(hwnd, msg, wparam, lparam, uid, data):
        if msg == WM_DPICHANGED:
            r = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
            user32.SetWindowPos(hwnd, None, r.left, r.top, r.right - r.left, r.bottom - r.top, 0x0004 | 0x0010)
            return 0  # NOZORDER | NOACTIVATE
        return comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)

    callback = SUBCLASSPROC(proc)
    _subclasses.append(callback)
    form = window.native
    hwnd = form.Handle.ToInt64()
    form.Invoke(Action(lambda: comctl32.SetWindowSubclass(wintypes.HWND(hwnd), callback, 1, 0)))


def apply_text_size(*windows):
    """The Settings' text size as the windows' zoom (all of Cladia's when none
    are named): WebView2's own, as a browser's Ctrl + +, so text, controls and the
    canvas viewers grow together (the viewers follow devicePixelRatio)."""
    from . import config
    factor = (config.setting("text_size") or 100) / 100
    if not WINDOWS:
        # GTK: WebKit's own zoom, set on GTK's thread
        from gi.repository import GLib
        from webview.platforms.gtk import BrowserView
        for window in windows or list(webview.windows):
            view = BrowserView.instances.get(window.uid)
            if view:
                GLib.idle_add(view.webview.set_zoom_level, factor)
        return
    from System import Action
    for window in windows or list(webview.windows):
        try:
            form = window.native
            control = form.browser.webview  # pywebview's EdgeChrome: the WebView2 control
            form.Invoke(Action(lambda control=control: setattr(control, "ZoomFactor", factor)))
        except Exception:  # a window closing, or not there yet: it gets it when it loads
            pass


def ask_before_closing(window, names, who, main=False):
    """Closing a window (names: the windows it closes) with unsaved changes asks
    first; False keeps it open. Runs on the window's own thread: the server is
    asked (edit locks carry the windows' unsaved state), not the pages.
    The main window also asks about running and waiting jobs: closing ends
    Cladia, so they are cancelled (their containers killed, not left running)."""
    from . import jobs
    from .request_context import unsaved_windows
    if unsaved_windows() & names and not window.create_confirmation_dialog(
            "Unsaved changes", f"{who} unsaved changes. Close anyway? (They are lost.)"):
        return False
    if not main:
        return True
    active = [j for j in jobs.JOBS.values() if j["status"] in ("running", "queued")]
    if not active:
        return True
    running = [j for j in active if j["status"] == "running"]
    names = ", ".join(j.get("title") or j["kind"] for j in running[:3]) + (" …" if len(running) > 3 else "")
    waiting = len(active) - len(running)
    text = (f"{len(running)} job{'s' if len(running) != 1 else ''} still running" + (f" ({names})" if running else "")
            + (f" and {waiting} waiting" if waiting else "") + ". Closing Cladia cancels unfinished work. Close anyway?")
    if not window.create_confirmation_dialog("Jobs still running", text):
        return False
    for job in active:
        jobs.cancel(job["id"])
    # Local transfers need to finish rollback/cleanup before daemon threads exit.
    # Forced termination is handled separately by their startup recovery journal.
    import time
    deadline = time.monotonic() + 15
    for job in active:
        if job["kind"].startswith("project-") and job.get("_finished"):
            if not job["_finished"].wait(max(0, deadline - time.monotonic())):
                try:
                    window.evaluate_js("toast('Project transfer is still stopping; Cladia will stay open until cleanup finishes.', true)")
                except Exception:
                    pass
                return False
    return True


def close_others(main):
    """The main window closed: the other Cladia windows go with it."""
    for window in list(webview.windows):
        if window is not main:
            window.destroy()


def bind(window):
    """Runs once the window is up: listens for files dropped on the page."""

    def on_drop(event):
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"addPaths({json.dumps(paths)})")  # same route as the Open dialog

    # prevent_default stops the window from handing the file to Windows (Geneious)
    window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)
