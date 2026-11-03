#!/bin/bash
# Entfernt nur den Gaming-Hilfsdienst. Gaming-Container, Spiele und Spielstände bleiben unangetastet.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo starten: sudo $0" >&2; exit 1; }
systemctl disable --now storchcam-gaming.service 2>/dev/null || true
rm -f /etc/systemd/system/storchcam-gaming.service
systemctl daemon-reload
rm -rf /opt/storchcam-gaming
echo "Hilfsdienst entfernt. Einstellungen bleiben in /etc/storchcam-gaming und /var/lib/storchcam-gaming."
