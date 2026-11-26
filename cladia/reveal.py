"""Showing a file or folder in the PC's file manager (Explorer, or a Linux desktop's)."""

import os
import subprocess
from pathlib import Path


def show(path, select=False):
    """Opens the folder; select=True: the file's folder with the file selected."""
    path = Path(path)
    if os.name == "nt":
        if select:
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(path)
        return
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if select:
        # The desktop's file manager selects it (GNOME Files, Dolphin, Nemo...); else just its folder
        uri = path.resolve().as_uri()
        try:
            if subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                               "--object-path", "/org/freedesktop/FileManager1",
                               "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                              timeout=10, **quiet).returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
        path = path.parent
    subprocess.Popen(["xdg-open", str(path)], **quiet)
