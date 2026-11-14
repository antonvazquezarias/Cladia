# Installing Cladia on Windows

[Overview](../README.md) · [User guide](USER_GUIDE.md)

Cladia’s supported desktop setup is Windows with WSL 2 for Linux analysis tools.
The Python application and its library live on Windows.

## Installer

If you have a lab-provided `Cladia-setup-<date>.exe`, run it.
Choose **Default** for Cladia and its standard tools, or **Custom** to select
optional tools and GetOrganelle databases. Each optional tool shows its approximate
installed size. Shared Linux files, package caches and databases require extra
space; database sizes vary. Selected extras download during setup, with overall
tool progress and live status in the installer. Downloads run without a console
window. When a stage cannot report a percentage, an animated bar shows activity.
Detailed setup output is saved in `%LOCALAPPDATA%\Cladia\setup.log`. If a tool
fails, choose Retry or Ignore to skip it and add it later in **Settings → Tools**.
The lab distributes installers through its *Ferramentas* folder. The installer
sets up Cladia for your user only (no administrator rights, except for WSL if
it is missing: Windows asks), downloads Python and
its packages the first time (a console window shows it, a few minutes), and adds
Cladia to the Start menu (and the desktop if you tick it). To update, run a newer
installer: your library and saved versions are preserved.
Close Cladia and let its jobs finish before updating or uninstalling. Setup checks
for Python processes using Cladia's environment before replacing files; if one is
still running, close it and retry. Uninstall also refuses to remove a running app.
Cladia remembers the folder its installer was run from (for the lab, the shared Ferramentas folder);
when a newer `Cladia-setup-<date>.exe` appears there, it says so when it starts,
and Settings › General shows the installer in Explorer. Uninstalling removes the program, its
Python and its Linux, not your library (`%USERPROFILE%\Cladia`).

The tools (MAFFT, annotation, trees, mapping, BLAST, primers) share one WSL 2
distribution named **Cladia**, stored in `%LOCALAPPDATA%\Cladia\linux`.
MFannot keeps an independent Linux root filesystem inside it (from its authors'
image on Docker Hub), and optional tools
(Settings → Tools: Clustal Omega, GetOrganelle, Krait, MUSCLE, MACSE, RAxML-NG, MrBayes, tRNAscan-SE, trimAl, ClipKIT) are downloaded from bioconda
into conda environments of their own when installed, so their dependencies stay
separate. Installing an optional tool needs an internet connection. Docker Desktop is not required. The first
tool run prepares the necessary environment; earlier tool installations
migrate automatically after their jobs and
terminals are closed. Each replacement is checked before its old registration
is removed. Your personal WSL distributions and the old shared wslc disk are
left alone. Uninstalling Cladia removes its managed Linux environments.

## From source

Download or clone this repository and open its folder.

1. Double-click [setup.bat](../setup.bat). If there's no Python 3.11 or newer, it downloads
   Python 3.14.4 from python.org and installs it quietly for your user only (no
   administrator rights needed). Then it creates the Python environment, installs
   packages, and checks for WSL in its own folder (`C:\Program Files\WSL\wsl.exe`);
   if it's missing it offers to install it (Windows asks for permission; then
   restart the PC and run setup.bat again). If that fails, install the latest
   `.msi` from [Microsoft’s WSL releases](https://github.com/microsoft/WSL/releases). Running it again only does
   what's still needed. The environment lives in `%LOCALAPPDATA%\Cladia\venv`,
   outside this folder, so replacing the program files with a new version keeps it.
2. Optional: double-click [check.bat](../check.bat). It makes (or checks) Cladia's Linux for
   the tools and for MFannot (a 1.7 GB download the first time), and runs MAFFT once.

## Starting Cladia and storing data

Start the app with [run.bat](../run.bat).
Cladia runs one copy at a time for its data folder: starting it again brings the
open window to the front instead of opening a second copy (use *New window* for a
second view of an item).
Everything Cladia holds lives in `%USERPROFILE%\Cladia`: `library` (one file per item
in the list) and `.cladia` (the list itself; job logs in `logs`; job folders: a
finished job's folder is removed once its results are in the list, failed ones when
Cladia next starts).
On the first launch after the rename, Cladia moves an existing library and its
settings folder to this location. It first checks that the earlier app is closed.
If a conflicting destination already exists, it leaves the original library in
place rather than overwriting it.

