#!/bin/sh
# Cladia's installer for Linux (Ubuntu 24.04 or newer, Debian 13 or newer): what
# the Windows installer does, for this user.
#
#   ./install.sh               install, or update an installed Cladia
#   ./install.sh --uninstall   remove Cladia (never your library, ~/Cladia)
#
# It runs from a copy of the program (a clone of the repository, or the folder
# that Cladia-linux-setup-<date>.sh unpacks). The program goes to
# ~/.local/share/Cladia/app and its Python to ~/.local/share/Cladia/venv; a
# Cladia entry goes to the applications menu and `cladia` to ~/.local/bin.
# The system packages it needs (Python, the GTK/WebKit window, Podman for the
# analysis tools) are installed with apt: the one step that asks for your password.
# The tools themselves are downloaded the first time one runs, as on Windows.
set -eu

HOME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/Cladia"
APP="$HOME_DIR/app"
VENV="$HOME_DIR/venv"
BIN="$HOME/.local/bin"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/cladia.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/cladia.png"
SRC=$(cd "$(dirname "$0")" && pwd)
PACKAGES="python3 python3-venv python3-gi gir1.2-webkit2-4.1 podman uidmap catatonit slirp4netns libglib2.0-bin xdg-utils fonts-noto-color-emoji"

say() { printf '%s\n' "$*"; }
fail() { printf 'Cladia setup: %s\n' "$*" >&2; exit 1; }

# ── Uninstall ─────────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  if command -v podman >/dev/null 2>&1 && podman container exists cladia 2>/dev/null; then
    if [ "$(podman container inspect --format '{{index .Config.Labels "cladia.owned"}}' cladia)" = "1" ]; then
      say "Removing Cladia's Linux container (the analysis tools)..."
      podman rm --force cladia >/dev/null
      podman rmi docker.io/library/debian:bookworm-slim >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$APP" "$VENV" "$HOME_DIR/linux" "$APP.new"
  rm -f "$BIN/cladia" "$DESKTOP" "$ICON"
  rmdir "$HOME_DIR" 2>/dev/null || true
  say "Cladia removed. Your library is still in ~/Cladia."
  exit 0
fi
[ -z "${1:-}" ] || fail "unknown option $1 (use --uninstall to remove Cladia)"
[ -f "$SRC/cladia/__main__.py" ] || fail "run this from Cladia's program folder"
[ "$(id -u)" != 0 ] || fail "run it as yourself, not as root (it asks for your password when needed)"
[ "$(uname -m)" = x86_64 ] || fail "Cladia's tools need a 64-bit Intel/AMD PC (x86_64)"

# ── 1. System packages ───────────────────────────────────────────────────
command -v apt-get >/dev/null 2>&1 || fail "this installer is for Ubuntu and Debian (apt)"
missing=""
for p in $PACKAGES; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
done
if [ -n "$missing" ]; then
  say "Installing system packages:$missing"
  say "(sudo asks for your password)"
  sudo apt-get update
  # shellcheck disable=SC2086
  # (with their recommended packages: rootless Podman needs its networking helpers)
  sudo apt-get install -y $missing
else
  say "System packages: already there."
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || \
  fail "Cladia needs Python 3.12 or newer (Ubuntu 24.04, Debian 13); this system has $(python3 --version)"

# Rootless Podman needs subordinate user ids for this user
if ! grep -q "^$(id -un):" /etc/subuid 2>/dev/null; then
  say "Giving $(id -un) the user ids Podman needs (sudo)..."
  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$(id -un)"
  podman system migrate >/dev/null 2>&1 || true
fi

# ── 2. The program ───────────────────────────────────────────────────────
say "Copying Cladia to $APP ..."
mkdir -p "$HOME_DIR"
rm -rf "$APP.new"
mkdir "$APP.new"
(cd "$SRC" && tar -cf - --exclude=./.git --exclude=./.lab-git --exclude=./build --exclude=./.claude \
  --exclude='__pycache__' --exclude=./.pytest_cache .) | tar -xf - -C "$APP.new"
if [ -n "${CLADIA_UPDATES_FROM:-}" ]; then
  printf '%s\n' "$CLADIA_UPDATES_FROM" > "$APP.new/UPDATES_FROM"  # where newer installers will be looked for
fi
rm -rf "$APP.old"
[ ! -d "$APP" ] || mv "$APP" "$APP.old"
mv "$APP.new" "$APP"
rm -rf "$APP.old"

# ── 3. Python and its packages (only when requirements.txt changed) ──────
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating the Python environment in $VENV ..."
  # system site packages: the GTK bindings (python3-gi) come from apt
  python3 -m venv --system-site-packages "$VENV"
fi
if cmp -s "$APP/requirements.txt" "$VENV/requirements.installed"; then
  say "Python packages: already up to date."
else
  say "Installing Python packages..."
  "$VENV/bin/python" -m pip install --upgrade --quiet pip
  "$VENV/bin/python" -m pip install -r "$APP/requirements.txt"
  cp "$APP/requirements.txt" "$VENV/requirements.installed"
fi

# ── 4. Starting it: `cladia` and the applications menu ───────────────────
mkdir -p "$BIN" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cat > "$BIN/cladia" <<EOF
#!/bin/sh
# Starts Cladia (cladia --browser: in the web browser instead of its own window)
cd "$APP" && exec "$VENV/bin/python" -m cladia "\$@"
EOF
chmod +x "$BIN/cladia"
"$VENV/bin/python" -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2])' \
  "$APP/cladia/static/cladia.ico" "$ICON"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Cladia
Comment=Sequences, alignments, annotation, trees and read mapping
Exec=env CLADIA_LOG_FILE=1 "$BIN/cladia"
Icon=$ICON
Terminal=false
Categories=Science;Biology;
StartupWMClass=Cladia
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$(dirname "$DESKTOP")" || true

# ── 5. Check that Podman runs containers for this user ───────────────────
if podman info >/dev/null 2>&1; then
  say "Podman: ready (Cladia prepares its own Linux the first time a tool runs)."
else
  say "Warning: 'podman info' failed, so the analysis tools may not run. Its message:"
  podman info 2>&1 | tail -5 || true
fi

say ""
say "Cladia is installed. Start it from the applications menu, or run: $BIN/cladia"
case ":$PATH:" in *":$BIN:"*) ;; *) say "($BIN is not on your PATH yet: log out and in again, or use the full path.)" ;; esac
say "To remove it: $APP/install.sh --uninstall"
