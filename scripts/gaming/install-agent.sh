#!/bin/bash
# Installiert den Gaming-Hilfsdienst von StorchCam auf dem Raspberry Pi (einmalig).
#
#   cd <StorchCam-Ordner> && sudo ./scripts/gaming/install-agent.sh [--screen] [--yes]
#
#   --screen  zusätzlich RetroArch und Emulator-Kerne aus den Paketen des Systems installieren,
#             damit man direkt am Bildschirm des Pi (HDMI) spielen kann (fragt vorher nach)
#   --yes     ohne Rückfrage installieren
#
# Der Dienst läuft als dein Benutzer (Gruppe docker), lauscht nur auf einem Unix-Socket und darf
# ausschließlich den Gaming-Container verwalten und RetroArch am Bildschirm starten und beenden.
# Er ändert keine anderen Container, Ports oder Daten.
set -euo pipefail

SCREEN=0
ASSUME_YES=0
for argument in "$@"; do
  case "$argument" in
    --screen) SCREEN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $argument (erlaubt: --screen, --yes)" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten: sudo $0" >&2
  exit 1
fi
SERVICE_USER="${SUDO_USER:-}"
if [ -z "$SERVICE_USER" ] || [ "$SERVICE_USER" = "root" ]; then
  echo "Bitte als normaler Benutzer mit sudo starten (nicht direkt als root)." >&2
  exit 1
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
USER_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"

command -v docker >/dev/null || { echo "Docker fehlt auf dem Pi. Erst Docker installieren." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 fehlt." >&2; exit 1; }
docker compose version >/dev/null || { echo "Das Docker-Compose-Plugin fehlt (docker compose)." >&2; exit 1; }
if ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx docker; then
  echo "Hinweis: $SERVICE_USER ist nicht in der Gruppe docker; der Dienst bekommt sie über systemd zusätzlich."
fi

# Spielen am Bildschirm: RetroArch und die Emulator-Kerne, die es für dieses System als Paket gibt.
if [ "$SCREEN" = 1 ]; then
  CANDIDATES="libretro-nestopia libretro-snes9x libretro-mgba libretro-gambatte libretro-genesisplusgx \
    libretro-beetle-pce-fast libretro-beetle-psx libretro-beetle-vb libretro-beetle-wswan libretro-desmume \
    libretro-bsnes-mercury-performance retroarch-joypad-autoconfig"
  echo "Paketlisten werden aktualisiert …"
  apt-get update -qq
  apt-cache show retroarch >/dev/null 2>&1 || { echo "Das Paket retroarch gibt es für dieses System nicht." >&2; exit 1; }
  PACKAGES="retroarch"
  for package in $CANDIDATES; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      PACKAGES="$PACKAGES $package"
    fi
  done
  echo "Für das Spielen am Bildschirm werden diese Pakete installiert:"
  echo "  $PACKAGES"
  if [ "$ASSUME_YES" != 1 ]; then
    answer=""
    read -r -p "Fortfahren? [j/N] " answer || true
    case "$answer" in
      j|J|ja|Ja|y|Y|yes) ;;
      *) echo "Abgebrochen; es wurde nichts installiert oder geändert."; exit 1 ;;
    esac
  fi
  # shellcheck disable=SC2086 # Paketnamen bewusst einzeln übergeben
  apt-get install -y $PACKAGES
  SERVICE_UID="$(id -u "$SERVICE_USER")"
  if [ ! -S "/run/user/$SERVICE_UID/wayland-0" ]; then
    echo "Hinweis: Für $SERVICE_USER läuft gerade keine Desktop-Sitzung (/run/user/$SERVICE_UID/wayland-0 fehlt)."
    echo "Am Bildschirm spielen geht nur, wenn $SERVICE_USER auch der Benutzer ist, der am Pi angemeldet ist."
  fi
fi

# Bis Version 0.x hieß das Projekt BI-StorchCam: alten Dienst ablösen und seine Einstellungen übernehmen.
# Es wird nichts gelöscht; die alten Ordner bleiben als Sicherung liegen.
if [ -f /etc/systemd/system/bi-storchcam-gaming.service ]; then
  systemctl disable --now bi-storchcam-gaming.service || true
  echo "Alter Dienst bi-storchcam-gaming gestoppt und deaktiviert. Der Gaming-Container läuft unverändert weiter."
  LEGACY_FOUND=1
fi
if [ -f /var/lib/bi-storchcam-gaming/state.json ] && [ ! -e /var/lib/storchcam-gaming/state.json ]; then
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /var/lib/storchcam-gaming
  cp -p /var/lib/bi-storchcam-gaming/state.json /var/lib/storchcam-gaming/state.json
  echo "Einrichtung aus /var/lib/bi-storchcam-gaming übernommen (Ordner, Port, Zugangsdaten)."
  LEGACY_FOUND=1
fi
if [ -f /etc/bi-storchcam-gaming/agent.json ] && [ ! -f /etc/storchcam-gaming/agent.json ]; then
  install -d -m 0755 /etc/storchcam-gaming
  sed 's#/var/lib/bi-storchcam-gaming#/var/lib/storchcam-gaming#g' /etc/bi-storchcam-gaming/agent.json > /etc/storchcam-gaming/agent.json
  echo "Einstellungen aus /etc/bi-storchcam-gaming/agent.json übernommen."
  LEGACY_FOUND=1
fi

install -d -m 0755 /opt/storchcam-gaming
install -m 0755 "$HERE/storchcam/gaming_agent.py" /opt/storchcam-gaming/agent.py
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /var/lib/storchcam-gaming
install -d -m 0770 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /var/lib/storchcam-gaming/socket
install -d -m 0755 /etc/storchcam-gaming
if [ ! -f /etc/storchcam-gaming/agent.json ]; then
  cat > /etc/storchcam-gaming/agent.json <<JSON
{
  "allowed_roots": ["$USER_HOME", "/mnt", "/media"],
  "state_dir": "/var/lib/storchcam-gaming",
  "socket_path": "/var/lib/storchcam-gaming/socket/agent.sock",
  "image": "lscr.io/linuxserver/retroarch:latest",
  "timezone": "$(cat /etc/timezone 2>/dev/null || echo Europe/Berlin)"
}
JSON
fi

cat > /etc/systemd/system/storchcam-gaming.service <<UNIT
[Unit]
Description=StorchCam Gaming-Hilfsdienst
After=docker.service
Wants=docker.service

[Service]
User=$SERVICE_USER
Group=$SERVICE_GROUP
SupplementaryGroups=docker
ExecStart=/usr/bin/python3 /opt/storchcam-gaming/agent.py --config /etc/storchcam-gaming/agent.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
UMask=0007

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable storchcam-gaming.service
# restart statt start: nach einem Update läuft sofort die neue Version des Dienstes.
systemctl restart storchcam-gaming.service
sleep 1
if [ "${LEGACY_FOUND:-0}" = 1 ]; then
  echo "Die alten Dateien bleiben zur Sicherheit liegen und können später von Hand entfernt werden:"
  echo "  /etc/systemd/system/bi-storchcam-gaming.service, /opt/bi-storchcam-gaming, /etc/bi-storchcam-gaming, /var/lib/bi-storchcam-gaming"
fi
if [ -S /var/lib/storchcam-gaming/socket/agent.sock ]; then
  echo "Gaming-Hilfsdienst läuft. Jetzt in StorchCam: Menü → Gaming."
else
  echo "Dienst gestartet, aber der Socket fehlt noch. Prüfen mit: journalctl -u storchcam-gaming -n 50" >&2
fi
