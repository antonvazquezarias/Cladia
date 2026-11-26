"""Starts Cladia: the API server in a background thread, then the app window.

    python -m cladia            # app window (pywebview)
    python -m cladia --browser  # use the default web browser instead

The app window opens first, with a "Starting Cladia…" page, while the rest
loads behind it (FastAPI and Biopython take about a second to import, while
.NET and the Edge WebView2 window start); then it goes to Cladia's page.
"""

import argparse
import hashlib
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import config

STARTING = """<!doctype html><html><body style="margin:0; height:100vh; display:grid; place-items:center;
  background:#f4f5f7; color:#687280; font:15px 'Segoe UI', system-ui, sans-serif">Starting Cladia…</body></html>"""

_instance = None  # the mutex handle, kept for the process's life (the OS releases it)
_legacy_instance = None
CLOSING_WAIT = 15  # s a new Cladia waits for one that is still closing


def take_instance(path=None, prefix="Cladia"):
    """True if this process may use the data folder, False if another Cladia has it.
    A Windows named mutex per data folder; the OS releases it when the process ends.
    Linux: a locked file in the data folder's .cladia, released the same way."""
    global _instance, _legacy_instance
    if os.name != "nt":
        import fcntl
        lock = Path(path or config.DATA_DIR) / ".cladia" / "instance.lock"
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock, "a")
        except OSError:
            return True  # couldn't check: don't stop Cladia for that
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        if prefix == "Cladia":
            _instance = handle
        else:
            _legacy_instance = handle
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    path = str(Path(path or config.DATA_DIR).resolve()).casefold()
    name = f"Local\\{prefix}-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # couldn't check: don't stop Cladia for that
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    if prefix == "Cladia":
        _instance = handle
    else:
        _legacy_instance = handle
    return True


def already_running():
    """Fallback when no window can be found (e.g. a --browser instance)."""
    message = (f"Cladia is already running for this data folder:\n{config.DATA_DIR}\n\n"
               "Use the window that is already open.")
    if sys.stdout:
        print(message)
    if os.name == "nt" and sys.stdout is None:  # run.bat: no console to read
        import ctypes
        user32 = ctypes.WinDLL("user32")
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW(None, message, "Cladia", 0x40)


def focus_existing():
    """Bring an open Cladia window to the front; False when there is none (a
    --browser instance, or a window not created yet). The title is 'Cladia' (the
    page's document.title adds ' · <item>')."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    found = []

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, len(cls))
        if cls.value == "#32770":  # a dialog, e.g. an earlier "already running" box
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == "Cladia" or title.value.startswith(("Cladia · ", "Cladia - ")):
                found.append((hwnd, title.value))
        return True

    user32.EnumWindows(callback_type(each), 0)
    if not found:
        return False
    hwnd = next((h for h, title in found if title == "Cladia"), found[0][0])
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    if not user32.SetForegroundWindow(hwnd):
        try:
            user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
            user32.SwitchToThisWindow(hwnd, True)  # Windows' own "bring to front"
        except AttributeError:
            return False
    return True


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    """Loads Cladia (the slow imports) and starts its server; returns when it answers.
    Only reachable from this PC (127.0.0.1)."""
    import uvicorn

    from . import jobs, library, projects, tool_install, tool_install_request
    from .server import app

    library.ensure_folders()
    tool_install.retire_plugins()  # plugin packages of older versions: their tools are built in now
    projects.recover_transfers()
    library.migrate_sidecar_figures()  # figures kept beside their source until 5 Nov 2026 become items
    library.fix_primer_modes()  # old primer items: design or test, for the two list sections
    jobs.clean_job_folders()
    try:
        tool_install_request.submit_pending()
    except Exception as exc:
        print(f"Could not queue optional-tool installation: {exc}")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    print(f"Cladia running at http://127.0.0.1:{port}/  (data folder: {config.DATA_DIR})")
    return thread


def main():
    parser = argparse.ArgumentParser(prog="cladia")
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of an app window")
    parser.add_argument("--port", type=int, default=0, help="port for the local server (default: any free port)")
    args = parser.parse_args()

    if config.DATA_DIR == config.LEGACY_DATA_DIR and config.DEFAULT_DATA_DIR != config.LEGACY_DATA_DIR:
        if not take_instance(config.LEGACY_DATA_DIR, "SeqLab"):
            print("Close SeqLab before Cladia moves your library to its new folder.")
            return
        try:
            config.migrate_default_data()
        except OSError as exc:
            print(f"Could not move the existing library yet: {exc}. Cladia will use it in place.")

    if not take_instance():  # one Cladia per data folder (before clean_job_folders)
        try:
            focused = focus_existing()
        except Exception:  # the guard must not stop Cladia for a focus problem
            focused = False
        if focused:
            return
        # no window: perhaps one just closed and its process is still ending
        # (opening Cladia again right after closing it); wait for it a while
        deadline = time.time() + CLOSING_WAIT
        while time.time() < deadline and not take_instance():
            time.sleep(0.3)
        if not _instance:
            already_running()  # still there, without a window (e.g. --browser): say where it is
            return

    if config.DATA_DIR == config.DEFAULT_DATA_DIR:
        try:
            config.migrate_default_data()  # retry a settings-folder move interrupted previously
        except OSError as exc:
            print(f"Could not rename the settings folder yet: {exc}. Cladia will use it in place.")

    # Started without a console (pythonw / run.bat, or the Linux applications menu,
    # whose entry sets CLADIA_LOG_FILE): send output to a log file
    if sys.stdout is None or os.environ.get("CLADIA_LOG_FILE") == "1":
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        log = open(config.APP_DIR / "cladia.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"

    if args.browser:
        thread = start_server(port)
        webbrowser.open(url)
        thread.join()
        return

    from .window import per_monitor_dpi
    per_monitor_dpi()  # before pywebview sets its own DPI mode (see window.py)
    if os.name != "nt":
        # Linux (GTK/WebKit): WebKit's GPU buffer sharing leaves the page blank where the
        # graphics driver can't do it (WSLg, some NVIDIA and virtual machines); without it
        # the page draws everywhere, and Cladia's canvas viewers don't need it
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

    import webview  # pywebview; uses Edge WebView2 on Windows

    from .window import NAMES, WindowApi, apply_text_size, ask_before_closing, bind, close_others, follow_dpi, set_icon

    window = webview.create_window("Cladia", html=STARTING, width=1400, height=900, min_size=(900, 600),
                                   background_color="#f4f5f7", js_api=WindowApi(url))

    # closing the main window closes Cladia's other windows; unsaved changes in any ask first
    window.events.closing += lambda: ask_before_closing(window, NAMES, "Some Cladia windows have", main=True)
    window.events.closed += lambda: close_others(window)
    window.events.shown += lambda: set_icon(window)
    window.events.shown += lambda: follow_dpi(window)  # moved to a screen with other scaling: resized

    def load(window):  # runs once the window is up: Cladia loads behind the "Starting" page
        start_server(port)
        window.load_url(url + "?w=main")
        window.events.loaded.wait(60)
        bind(window)  # drag-and-drop belongs to the page: after Cladia's is there
        apply_text_size(window)
        from .request_context import AFTER_SETTINGS
        AFTER_SETTINGS.append(apply_text_size)  # a new text size: every window at once

    # private_mode=False keeps browser storage between runs (view choices, genetic codes).
    # Returns when the window is closed. Then Cladia ends at once: winding down
    # (the server thread, .NET, WebView2) took ~6 s, during which the process kept
    # its one-copy lock, so opening Cladia again right away was refused.
    webview.start(load, (window,), private_mode=False)
    jobs = sys.modules.get("cladia.jobs")  # jobs cancelled on closing: their logs saved first
    until = time.time() + 3
    while jobs and time.time() < until and any(j["status"] in ("running", "queued") for j in jobs.JOBS.values()):
        time.sleep(0.1)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
