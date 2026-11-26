# Installing Cladia on Linux

[Overview](../README.md) · [User guide](USER_GUIDE.md) · [Windows](INSTALLATION.md)

Cladia runs on Ubuntu 24.04 or newer and Debian 13 or newer, on 64-bit Intel/AMD
PCs (x86_64), with a desktop (GNOME, KDE and others). Its analysis tools run in a
Podman container that Cladia makes and manages itself. Other distributions may work
from source but haven't been tested.

## Installer

Download `Cladia-linux-setup-<date>.sh` from the
[latest release](https://github.com/antonvazquezarias/Cladia/releases/latest), then
in a terminal:

```sh
sh ~/Downloads/Cladia-linux-setup-<date>.sh
```

It installs Cladia for your user:

- **System packages:** the ones Cladia needs are installed with `apt`: Python, the
  GTK/WebKit window, Podman and its user-id tools. This is the one step that asks
  for your password (`sudo`). Packages that are already there are skipped.
- **The program and its Python:** the program goes in
  `~/.local/share/Cladia/app` and its Python packages in `~/.local/share/Cladia/venv`.
- **Starting it:** Cladia is added to the applications menu, and a `cladia` command
  to `~/.local/bin`.

Then start Cladia from the applications menu. `cladia --browser` opens it in your
web browser instead.

To update, run a newer installer: your library and saved versions are kept. Cladia
remembers the folder its installer was run from (for example a lab's shared
folder). When a newer `Cladia-linux-setup-<date>.sh` appears there, Cladia says so
when it starts.

To uninstall:

```sh
sh ~/.local/share/Cladia/app/install.sh --uninstall
```

This removes the program, its Python and its tools container, not your library
(`~/Cladia`).

Optional tools (Clustal Omega, GetOrganelle, MUSCLE and the others) are installed
from **Settings › Tools** once Cladia is running. The Windows installer's *Custom*
choice has no Linux equivalent.

## The analysis tools

The tools run in one rootless Podman container named **cladia**, made from Debian
the first time a tool runs. That first run takes a few minutes, and the job's bar
says so. The container holds the same tools, conda environments and image-based
engines (MFannot) as the WSL distribution on Windows. Your files are visible inside
it under `/host`. Podman stores the container with your user's other containers,
in `~/.local/share/containers`. Your own containers are left alone.

If a job says Podman is missing, install it with `sudo apt install podman`, or run
the installer again. `podman info` in a terminal shows whether Podman works for
your user.

## From source

Clone this repository, then run `sh install.sh` in its folder. It does the same as
the installer, from that copy.

## Starting Cladia and storing data

Cladia runs one copy at a time for its data folder. Everything it holds lives in
`~/Cladia`:

- `library`: one file per item in the list.
- `.cladia`: the list itself, job logs in `logs`, and `cladia.log` when Cladia is
  started from the menu.
