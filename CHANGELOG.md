# Änderungen

Alle nennenswerten Änderungen an StorchCam. Versionsnummern folgen [Semantic Versioning](https://semver.org/lang/de/).

## [1.0.0] – 2026-11-02

Erste öffentliche Version.

### Anzeige
- Live-Cam im Vollbild im eigenen Player (YouTube-Livestreams und HLS über hls.js, normale YouTube-Videos als stumme Schleife, andere Seiten eingebettet); Antippen und Menü halten das Bild nie an, bei Aussetzern verbindet sich der Player selbst neu.
- Kamera-Favoriten mit automatischem Ersatz, wenn eine Kamera ausfällt, und Suche nach Streams, die gerade auf YouTube live sind.
- Infoleiste mit Uhr, Wetter (Open-Meteo, Streifen für die nächsten Stunden), amtlichen Unwetterwarnungen (DWD über Bright Sky) und VRR-Abfahrten als Losgeh-Zeit.
- Themes `auto`, `dark`, `light`, `high-contrast`, `y2k`, `dagobert`, `flap`, `paper` und `plasma`; Leseabstand, Hoch- und Querformat, Nachtmodus mit Zeitplan und Einbrennschutz.

### Bedienung
- Touch-Menü im iOS-Stil: Startseite mit „Läuft gerade“ und Kacheln, Seiten gleiten von rechts herein, Zurück per Knopf oder Wisch vom linken Rand.
- Große Auswahlblätter statt kleiner Dropdowns, Bildschirmtastatur, kein versehentliches Markieren von Text.
- Einrichtung beim ersten Start in drei Schritten (Kamera, Ort, Haltestellen).
- Am Handy oder Laptop einstellen: QR-Code und einmaliger Kopplungscode.

### Musik
- Internetradio (Radio-Browser), YouTube Music und Spotify mit Suche, Alben-, Playlist- und Künstlerseiten.
- Player mit Zeitleiste, Zufall, Wiederholen, Warteschlange, Ausschalt-Timer und Klangregler mit sechs Bändern.
- Songkarte oben rechts mit Lautstärke, Mini-Player im Menü, Bluetooth-Lautsprecher.
- Sofortige Rückmeldung beim Abspielen; die nächsten Titel werden vorab aufgelöst.

### Gaming
- RetroArch (`lscr.io/linuxserver/retroarch`) als eigener Container, eingerichtet und verwaltet über das Menü: Speicherordner wählen, Port prüfen, Start/Stopp, Spiele hochladen, Update mit Rückweg zur vorigen Version.
- Gespielt wird direkt am Pi-Bildschirm (HDMI): RetroArch läuft im Vollbild über der Kamera, Controller per USB oder Bluetooth (Koppeln im Menü), Start und Beenden im Menü oder am Handy. Einmalig `sudo ./scripts/gaming/install-agent.sh --screen`.
- Oder im Browser eines anderen Geräts im Heimnetz (Adresse und QR-Code im Menü).
- Ein kleiner Hilfsdienst auf dem Pi (`scripts/gaming/install-agent.sh`) übernimmt die Docker-Befehle; StorchCam selbst bekommt keinen Zugriff auf den Docker-Socket.

### Betrieb
- Docker-Container mit Chromium-Kiosk auf Wayland, Healthcheck und automatischem Neustart; Port über `STORCHCAM_PORT` wählbar.
- Atomare Konfiguration mit Sicherung, rotierende Logs, PBKDF2-gehashte PIN außerhalb von Docker.

### Umbenennung
- Das Projekt hieß vorher BI-StorchCam. Alte Umgebungsvariablen (`BI_STORCHCAM_…`), der alte Konfigurationsordner und eine vorhandene Gaming-Einrichtung werden weiter erkannt; siehe „Update von einer Version vor 1.0“ in der README.
