# Gaming

StorchCam kann ein Retro-Gaming-System als eigenen Docker-Container einrichten und verwalten.
Gespielt wird entweder direkt am Bildschirm des Pi (HDMI) mit Controller am Pi, oder im Browser eines
anderen Geräts (Laptop, Tablet, Handy) mit dem Controller an genau diesem Gerät.

## Was verwendet wird

| | |
|---|---|
| Image | `lscr.io/linuxserver/retroarch:latest` (ARM64 und x86-64, Ubuntu 24.04, RetroArch 1.22.2 zum Zeitpunkt der Umsetzung) |
| Oberfläche | RetroArch im Browser über Selkies (Teil des Images) |
| Container | `storchcam-gaming` (Compose-Projekt `storchcam-gaming`) |
| Port | `3001` auf dem Pi → `3001` im Container (HTTPS, selbstsigniertes Zertifikat); im Assistenten änderbar |
| Anmeldung | Benutzer `spieler` und ein zufälliges Passwort (Basic Auth des Images), im Gaming-Menü sichtbar |

Pegasus Frontend wird nicht verwendet: Es gibt dafür kein gepflegtes Docker-Image und keinen
64-Bit-ARM-Desktop-Build für den Pi 5. Das wurde vor der Umsetzung abgestimmt.

## Aufbau

```
StorchCam (Container, Benutzer 1000)
   │  Unix-Socket /run/gaming-agent/agent.sock   (kein Docker-Socket im Container)
   ▼
Gaming-Hilfsdienst (systemd auf dem Pi, scripts/gaming/install-agent.sh)
   │  docker … mit festen Argumentlisten
   ▼
Container storchcam-gaming  (lscr.io/linuxserver/retroarch)
```

Der Hilfsdienst (`storchcam/gaming_agent.py`, nur Python-Standardbibliothek) kann ausschließlich:
Status lesen, freigegebene Ordner auflisten und anlegen, Ports prüfen, Gaming installieren, den
Gaming-Container starten und stoppen, aktualisieren und auf die vorige Version zurückstellen,
einen vorhandenen RetroArch-Container übernehmen sowie Spieldateien auflisten und speichern.
Er löscht weder Container noch Daten und fasst keine anderen Container an.

## Einrichtung auf dem Pi (einmalig)

```bash
cd StorchCam                # der Ordner, in dem StorchCam liegt
sudo ./scripts/gaming/install-agent.sh
docker compose up -d        # bindet den Socket-Ordner in StorchCam ein
```

Das Skript installiert den Dienst `storchcam-gaming.service` (läuft als dein Benutzer mit der
Gruppe `docker`), legt `/etc/storchcam-gaming/agent.json` und `/var/lib/storchcam-gaming/` an.
Freigegebene Ordner sind standardmäßig dein Home-Verzeichnis, `/mnt` und `/media` (`allowed_roots`
in der agent.json). Entfernen: `sudo ./scripts/gaming/uninstall-agent.sh` (Container, Spiele und
Spielstände bleiben).

Danach im Menü auf **Gaming** tippen, **Gaming installieren**, einen Ordner wählen oder anlegen,
Port prüfen lassen und bestätigen. Die Schritte (Ordner, Port, Image laden, Container, Oberfläche)
zeigen den echten Fortschritt.

## Am Pi-Bildschirm spielen (HDMI)

Am Bildschirm des Pi läuft RetroArch direkt als Programm im Vollbild, ohne Umweg über den Browser. Das ist
flüssiger als der Stream, weil der Pi das Bild nicht erst kodieren muss.

**Einmalig installieren** (im StorchCam-Ordner auf dem Pi):

```bash
sudo ./scripts/gaming/install-agent.sh --screen
```

Das Skript aktualisiert den Hilfsdienst, prüft, welche Pakete es für das System gibt (`retroarch` und
Emulator-Kerne wie `libretro-snes9x`, `libretro-mgba`, `libretro-nestopia`, `libretro-genesisplusgx`),
zeigt die Liste und installiert erst nach Bestätigung (`--yes` überspringt die Frage). Pakete, die es für
das System nicht gibt, werden übersprungen.

**Spielen**: Gaming-Menü → **Am Pi-Bildschirm spielen**, am Bildschirm selbst oder am Handy. RetroArch legt
sich über die Kamera, die im Hintergrund weiterläuft. Spiele lädt man in RetroArch über „Inhalte laden“; der
Dateibrowser beginnt im Ordner `GAMING/roms`.

- **Controller**: USB einfach einstecken. Bluetooth-Controller im Menü unter **Controller per Bluetooth
  koppeln** (Konten und Lautsprecher → Bluetooth) suchen und verbinden; danach verbinden sie sich beim
  Einschalten von selbst. RetroArch erkennt bekannte Controller automatisch (udev).
- **Zurück zur Kamera**: in RetroArch **Beenden** wählen, oder im StorchCam-Menü (am Handy)
  **Am Pi-Bildschirm beenden**. Ein Menü-Knopf am Controller lässt sich in RetroArch unter Einstellungen →
  Eingabe → Hotkeys festlegen.
- **Spielstände**: Spielstände und Save States liegen in `GAMING/saves` und `GAMING/states`, BIOS-Dateien
  in `GAMING/bios`, also in denselben Ordnern wie beim Gaming-Container. Ein Spiel nicht gleichzeitig am
  Bildschirm und im Browser spielen.
- **Einstellungen**: RetroArch speichert sie in `GAMING/pi/retroarch.cfg` (beim ersten Start aus
  `/etc/retroarch.cfg` übernommen). Die Ordner-Zeilen schreibt StorchCam bei jedem Start in
  `GAMING/pi/storchcam.cfg`. Ausgaben des letzten Starts stehen in `/var/lib/storchcam-gaming/retroarch.log`.
- **Voraussetzung**: Der Hilfsdienst läuft als derselbe Benutzer, der am Pi angemeldet ist (Desktop-Autologin).
  Sonst meldet das Menü „Kein Bildschirm gefunden“.
- Der Nachtmodus schaltet das Licht des Bildschirms nicht ab, solange am Bildschirm gespielt wird.
- Für das Spielen am Bildschirm muss der Gaming-Container nicht laufen; **Stoppen** spart Leistung, wenn
  niemand im Browser spielt.

## Speicherordner

```
GAMING/
├── config/   → /config                               Einstellungen, Kerne (Home im Container)
├── roms/     → /config/roms                          Spiele
├── bios/     → /config/.config/retroarch/system      BIOS-Dateien (selbst hinzufügen)
├── saves/    → /config/.config/retroarch/saves       Spielstände
└── states/   → /config/.config/retroarch/states      Save States
```

`/config` stammt aus der Image-Dokumentation. Die drei RetroArch-Pfade sind die üblichen
Standardordner von RetroArch unter Linux; ob das Image sie genauso nutzt, muss auf dem Pi geprüft
werden (in RetroArch: Einstellungen → Verzeichnisse). Vorhandene Dateien im gewählten Ordner bleiben
erhalten. Es werden keine Spiele oder BIOS-Dateien heruntergeladen.

## Bedienung

- **Gaming öffnen** (auf einem anderen Gerät): zeigt Adresse, QR-Code, Benutzer und Passwort. Am Zielgerät
  öffnen; beim ersten Mal das selbstsignierte Zertifikat bestätigen. Der Browser braucht HTTPS für Ton, Bild
  und Controller. Die Adresse nutzt die IP des Pi im Heimnetz (Namen wie `raspberrypi.local` kennen manche
  Handys nicht). Am Pi-Bildschirm springt dieser Knopf zum QR-Code; gespielt wird dort über
  **Am Pi-Bildschirm spielen**.
- **Hilfsdienst aktualisieren**: nach einem StorchCam-Update `sudo ./scripts/gaming/install-agent.sh`
  erneut ausführen; Einstellungen und Daten bleiben, der Dienst startet neu. Eine Einrichtung aus der Zeit
  vor Version 1.0 (Dienst `bi-storchcam-gaming`) wird dabei übernommen: Der alte Dienst wird gestoppt, der
  Gaming-Container behält seinen Namen und läuft weiter, die alten Dateien bleiben liegen.
- **Spiele hinzufügen**: Dateien vom Gerät hochladen (bis 4 GB je Datei, bekannte Endungen); sie
  landen in `GAMING/roms`. In RetroArch: Inhalte laden → `/config/roms`.
- **Kerne**: werden in RetroArch selbst geladen (Hauptmenü → Online-Updater → Core-Downloader),
  sofern RetroArch sie für ARM64 anbietet.
- **Starten/Stoppen**, **Nach Update suchen** (bisherige Version wird als
  `storchcam-gaming/retroarch:previous` gesichert) und **Vorige Version**.
- **Kamera anhalten, während Gaming läuft** (`gaming.pause_camera`): der Kiosk hält das Kamerabild an,
  solange der Gaming-Container läuft, und startet es danach wieder.

## Stand der Prüfung

**In dieser Umgebung getestet** (automatische Tests, gespieltes Docker):
Ordnerwahl mit Pfadprüfung, Installationsschritte inklusive Fehlern (Port belegt, Download fehlgeschlagen,
nichts wird gelöscht), Compose-Datei, Start/Stopp nur des Gaming-Containers, Übernehmen eines vorhandenen
RetroArch-Containers, Update mit Sicherung und Zurückstellen, Upload und Liste der Spiele, Kommunikation
über den Unix-Socket, alle StorchCam-Endpunkte mit Anmeldung, die Gaming-Seite im Menü und das
Anhalten der Kamera. Für den Pi-Bildschirm: Start und Beenden von RetroArch mit gespieltem Programmstart
(Aufruf, Wayland-Umgebung, Ordner in der Konfiguration, Fehler beim Start), die Menü-Karte, das Koppeln von
Bluetooth-Controllern mit gespieltem `bluetoothctl` und dass der Nachtmodus das Licht beim Spielen anlässt.

**Aus Code und Dokumentation abgeleitet, noch nicht auf dem Pi geprüft**:
dass das Image auf dem Pi 5 startet, die Pfade für Spielstände und BIOS, welche Emulator-Kerne es für
ARM64 gibt, Ton und Bild im Browser, Controller über den Browser, die Leistung beim Streaming. Für den
Pi-Bildschirm: welche libretro-Pakete Raspberry Pi OS anbietet (das Skript prüft das vor dem Installieren),
dass RetroArch aus dem Hilfsdienst heraus auf dem Wayland-Desktop (labwc) im Vordergrund erscheint, Ton über
PipeWire und dass Controller per udev ohne weitere Rechte lesbar sind.

**Noch auf Pi und Endgeräten zu testen**: Installation über das Menü, Spiel starten am Pi-Bildschirm und
an anderen Geräten, Controller (Xbox,
DualShock/DualSense) am Laptop/Tablet, Spielstände nach Neustart, Stoppen und Starten, Update, und dass
andere Container auf dem Pi und StorchCam unbeeinträchtigt laufen.

## Bekannte Einschränkungen

- Der Pi 5 hat keinen Hardware-Video-Encoder; das Streaming kostet CPU zusätzlich zum Emulator.
  Anspruchsvolle Systeme (PS2 und neuer) sind nicht zu erwarten.
- Selbstsigniertes Zertifikat: jedes Gerät muss es einmal bestätigen. Smart-TV-Browser unterstützen oft
  kein WebCodecs oder keine Gamepad-API.
- Die Oberfläche enthält laut Image-Doku ein Terminal mit Root-Rechten im Container; deshalb immer mit
  Passwort und nur im Heimnetz betreiben (Port nicht im Router freigeben).
- Geräte-Kopplung mit Empfängerseite und Controller-Test sind für eine spätere Version geplant.
