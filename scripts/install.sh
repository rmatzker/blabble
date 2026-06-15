#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${HOME}/.local/share/dictate/venv"
CONFIG_DIR="${HOME}/.config/dictate"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
APPS_DIR="${HOME}/.local/share/applications"

missing=()
command -v wtype >/dev/null    || missing+=(wtype)
command -v paplay >/dev/null   || missing+=(libpulse)
command -v python3 >/dev/null  || missing+=(python)

if (( ${#missing[@]} )); then
    echo "Missing system packages: ${missing[*]}"
    echo "Install with: sudo pacman -S ${missing[*]}"
    exit 1
fi

echo "[1/4] Creating venv at $VENV_DIR"
mkdir -p "$(dirname "$VENV_DIR")"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -e "$REPO_DIR"

echo "[2/4] Setting up config at $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
    cp "$REPO_DIR/config.toml.example" "$CONFIG_DIR/config.toml"
    echo "  → wrote $CONFIG_DIR/config.toml"
else
    echo "  → $CONFIG_DIR/config.toml already exists, leaving it"
fi

echo "[3/4] Installing systemd user unit + desktop entry"
mkdir -p "$SYSTEMD_DIR"
sed "s|@VENV@|$VENV_DIR|g" "$REPO_DIR/systemd/dictate.service" > "$SYSTEMD_DIR/dictate.service"
# The desktop entry gives dictate an XDG app id. xdg-desktop-portal >= 1.22
# requires one to open a GlobalShortcuts session (else CreateSession fails
# with "An app id is required"). The file basename ('dictate') is the app id.
mkdir -p "$APPS_DIR"
sed "s|@VENV@|$VENV_DIR|g" "$REPO_DIR/share/dictate.desktop" > "$APPS_DIR/dictate.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now dictate.service

echo "[4/4] Done"
echo
echo "Status:  systemctl --user status dictate"
echo "Logs:    journalctl --user -u dictate -f"
echo
echo "First-time hotkey: KDE will prompt to confirm Meta+Space."
echo "Rebind any time:   System Settings → Shortcuts → search 'dictate'."
