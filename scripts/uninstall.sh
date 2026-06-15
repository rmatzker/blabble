#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="${HOME}/.local/share/dictate/venv"
CONFIG_DIR="${HOME}/.config/dictate"
DATA_DIR="${HOME}/.local/share/dictate"
SYSTEMD_DIR="${HOME}/.config/systemd/user"

echo "Stopping & disabling service"
systemctl --user disable --now dictate.service 2>/dev/null || true
rm -f "$SYSTEMD_DIR/dictate.service"
rm -f "${HOME}/.local/share/applications/dictate.desktop"
systemctl --user daemon-reload

echo "Removing venv: $VENV_DIR"
rm -rf "$VENV_DIR"

read -rp "Also delete config and recordings ($CONFIG_DIR, $DATA_DIR)? [y/N] " ans
case "${ans,,}" in
    y|yes)
        rm -rf "$CONFIG_DIR" "$DATA_DIR"
        echo "  removed"
        ;;
    *)
        echo "  kept"
        ;;
esac

echo "Done."
