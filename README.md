# StorchCam

StorchCam ist ein leichtgewichtiger lokaler Web-Kiosk für den Raspberry Pi: eine beliebige Live-Cam im Vollbild mit Uhr, Wetter, Unwetterwarnungen, optionalen VRR-Abfahrten, Musik (Radio, YouTube Music, Spotify) und Retro-Gaming. Die Kamera füllt den ganzen Bildschirm; alle Zusatzinformationen liegen in einer Infoleiste am unteren Rand, bedient wird per Touch.

Wie die Oberfläche aussieht und sich bedienen lässt, zeigt die klickbare Vorschau in [docs/vorschau/](docs/vorschau/).

> **Version 1.0.0** – erste öffentliche Version. Download als ZIP unter [Releases](../../releases), Änderungen in [CHANGELOG.md](CHANGELOG.md). Gaming ist neu und noch nicht auf jeder Pi-Konfiguration erprobt; was geprüft ist und was nicht, steht in [docs/GAMING.md](docs/GAMING.md).

## Screenshots

Die automatisierten UI-Tests prüfen die Anzeige bei 800×480, 1024×600, 1280×720, 1920×1080 und im Hochformat, dazu Musik-Seiten, Player-Reiter und Gesten, und speichern bei Fehlern Screenshots. Produktive Referenzscreenshots werden nach dem ersten bestandenen Pi-Hardwaretest ergänzt.

## Funktionen

- eine einzige Web-Kiosk-Architektur und ein zentraler Einstiegspunkt
- vollflächiger Livestream im eigenen Player: YouTube-Livestreams und HLS-Quellen laufen über hls.js, normale YouTube-Videos als stumme Schleife, beides in einem video-Element ohne YouTube-Bedienelemente. Antippen, Menü und Dialoge halten das Bild nicht an, bei Aussetzern bleibt das letzte Bild stehen und der Player verbindet sich selbst neu. Andere Seiten werden wie bisher eingebettet
- Kamera-Favoriten: Die Reihenfolge bestimmt den Ersatz, wenn eine Kamera ausfällt; die App wechselt zurück, sobald die erste wieder läuft
- Infoleiste unten: Uhr mit Datum und Statuszeile, Wetter mit Streifen für die nächsten Stunden (Temperaturkurve, Regenbalken, Regenschwelle, Sonnenauf- und -untergang) und die nächsten Abfahrten als Losgeh-Zeit (Abfahrt minus Gehzeit) mit Verspätung und Linienform nach Verkehrsmittel. Antippen öffnet Wetter für 24 Stunden bzw. alle Abfahrten. Nach einer einstellbaren Zeit ohne Berührung wird die Leiste zur schmalen Kompaktleiste; Wischen nach unten verkleinert oder blendet sie aus, nach oben holt sie zurück
- amtliche Unwetterwarnungen des Deutschen Wetterdienstes über Bright Sky, ab einstellbarer Stufe
- Leseabstand (etwa 1 m, 2 m, 3 m und mehr) statt Layoutprofilen; die Anzeige passt sich außerdem Hoch- und Querformat an
- Themes `auto` (hell am Tag, dunkel nachts), `dark`, `light`, `high-contrast`, `y2k` mit Wolken am Tag und Sternenhimmel bei Nacht, `dagobert` im Comic-Stil, `flap` (Fallblatt mit umklappenden Ziffern), `paper` (ruhig wie E-Ink) und `plasma` (dunkles Industrieblau mit wanderndem Lichtstrahl)
- Nachtmodus mit Zeitplan: dimmt den Bildschirm oder schaltet, wo das System es erlaubt, die Hintergrundbeleuchtung ab; Antippen weckt ihn für fünf Minuten. Einbrennschutz: die Leiste wandert alle drei Minuten um einen Pixel
- kleine Animationen (Seitenwechsel von rechts, gleitende Auswahl, federnde Schalter, kurzes Eindrücken beim Antippen, Equalizer, Platzhalter beim Laden), nur über `transform` und `opacity`; bei „reduzierte Bewegung“ aus
- Schrift Barlow lokal eingebunden (SIL OFL 1.1), funktioniert offline
- Open-Meteo-Wetter ab der aktuellen Uhrzeit, Regenstunden nach den konfigurierten Schwellen
- mehrere VRR-Haltestellen mit Linien- und Nachtbusfiltern; Abfahrten aller Haltestellen nach Minuten sortiert
- Menü-Knopf oben links (oder `Ctrl+Alt+S`) mit Touch-Oberfläche und Bildschirmtastatur
- Livestream-Auswahl aus allem, was gerade auf YouTube live ist, ohne API-Schlüssel
- weltweite Ortssuche; Uhr und Wetter folgen der Zeitzone des Orts
- Musik wie in einer App: Internetradio (Sendersuche über die freie Radio-Browser-Datenbank, Wiedergabe über mpv), YouTube Music und Spotify mit „Weiterhören“ über alle Quellen, Lieblingssendern bzw. Playlists und einer Suche nach Songs, Künstlern, Alben und Playlists. Ein Song spielt nur sich selbst; Album, Playlist und Künstler öffnen eine eigene Seite mit allen Titeln, „Abspielen“ und „Zufall“, Antippen eines Titels spielt die Liste ab dort. Zurück über den Knopf oder mit einem Wisch vom linken Rand
- Player: großes Cover mit bewegtem Equalizer, Zeitleiste zum Spulen, Zufall, Wiederholen (alle, einen), Gefällt mir, Zu Playlist hinzufügen, Lautstärke, Ausgabe, Ausschalt-Timer (15, 30, 60 Minuten oder bis zum Nachtmodus) und die Warteschlange zum Springen, Entfernen und Verschieben. Ein Klangregler mit sechs Bändern und Voreinstellungen (Mehr Bass, Pop, Rock, Klassik, Sprache, Akustik, Nachts leise) wirkt auf alles, was der Pi über mpv spielt. Beim Radio zeigt der Player den laufenden Song aus dem Stream mit Cover (iTunes-Suche) und „Zuletzt im Radio“; Vor und Zurück wechseln den Lieblingssender. Spotify: Warteschlange nur zum Ansehen, kein Klangregler
- Songkarte oben rechts: klein nur Bild und Titel, antippen für groß mit Bedienung; beim Radio Sendername, „Jetzt: Künstler – Titel“ und Equalizer statt Fortschritt. YouTube Music und Spotify brauchen Premium; Ton über Bluetooth-Lautsprecher (Suchen, Koppeln, Trennen im Menü)
- Einrichtung beim ersten Start in drei Schritten: Kamera, Ort, Haltestellen
- am Handy einstellen: Menü → „Am Handy“ zeigt QR-Code und sechsstelligen Code (10 Minuten gültig, einmalig); danach steht das ganze Menü am Handy zur Verfügung
- im Docker-Betrieb am Bildschirm ohne PIN, Geräte im Heimnetz nur mit Kopplungscode oder PIN; sonst PBKDF2-gehashte PIN und zeitlich begrenzte Admin-Sessions
- Hintergrund-State ohne Provideraufrufe pro Browseranfrage
- überwachte Chromium-, Chrome- oder Edge-Prozesse mit begrenztem Backoff
- atomische Konfiguration mit letzter Sicherung
- rotierende App- und Browserlogs
- Docker-Container mit Healthcheck und automatischem Neustart

## Betrieb

StorchCam läuft als Docker-Container (getestet auf Raspberry Pi OS / Debian 13, ARM64, labwc/Wayland). Der Container startet Server und Chromium-Kiosk; Chromium erscheint direkt auf dem angeschlossenen Bildschirm.

## Bekannte Einschränkungen

- YouTube-Livestreams löst die App mit yt-dlp in eine HLS-Adresse auf (braucht im Container Deno, siehe Dockerfile). Ändert YouTube etwas, das yt-dlp noch nicht kennt, fällt die App auf die Einbettung zurück; ein Update von yt-dlp behebt das meist. Das Abspielen außerhalb des YouTube-Players weicht von den YouTube-Nutzungsbedingungen ab; für den privaten Kiosk ist das eine bewusste Entscheidung.
- Die Hintergrundbeleuchtung lässt sich nur schalten, wenn `/sys/class/backlight/*/bl_power` beschreibbar ist; im Container meist nicht. Dann dimmt der Nachtmodus nur das Bild.
- DWD-Warnungen gibt es nur für Orte in Deutschland.
- Andere Stream-Plattformen kann die App nur einbetten; sie können das per CSP oder `X-Frame-Options` blockieren. Ein geladenes `iframe` wird deshalb nicht als bestätigte Wiedergabe bezeichnet.
- ÖPNV basiert aktuell auf dem VRR-Haltestellenmonitor. Andere Regionen werden nicht allgemein als unterstützt beworben.
- Wayland-Displayrotation wird nicht automatisch verändert. Hardwareprofile wenden Rotation nur sicher unter X11 an.
- Windows stellt Temperaturwerte nur dar, wenn sie tatsächlich verfügbar sind; andernfalls erscheint `–` beziehungsweise „nicht verfügbar“.
- Gaming (RetroArch im Browser eines anderen Geräts) ist neu; Emulator-Kerne, Controller und Leistung hängen vom Pi und vom Endgerät ab. Details in [docs/GAMING.md](docs/GAMING.md).

## Externe Dienste

Für Live-Daten benötigt StorchCam Netzwerkzugriff zu YouTube und googlevideo.com (Livestream über den lokalen Proxy), Open-Meteo (Wetter, Ortssuche, Zeitzone), Nominatim/OpenStreetMap (Adresssuche), dem VRR-Haltestellenmonitor, Bright Sky (DWD-Warnungen), der Radio-Browser-Datenbank und den Streams der gewählten Radiosender, für die Musikanzeige zu Spotify bzw. YouTube Music (Google) und für die Livestream-Suche zu YouTube (öffentliche Suchseite bzw. YouTube Data API, Vorschaubilder von i.ytimg.com). Der Stream benötigt Zugriff auf die konfigurierte Streamdomain. Die App enthält keine Cloud-Benutzerverwaltung und sendet keine lokale PIN.

## Docker

Voraussetzung: Raspberry Pi (getestet mit Pi 5) mit Raspberry Pi OS (64 Bit, Desktop), Docker und dem Compose-Plugin.

Die ZIP-Datei der neuesten Version unter [Releases](../../releases) herunterladen und auf dem Pi entpacken, oder das Repository klonen:

```bash
git clone <Adresse dieses Repositorys> StorchCam
cd StorchCam
mkdir -p data
docker compose up -d --build
```

Mehr ist nicht nötig: kein API-Schlüssel, keine PIN. Der Container startet Server und Chromium-Kiosk; die Anzeige erscheint im Vollbild direkt auf dem angeschlossenen Bildschirm. Zusätzlich ist sie auf dem Gerät selbst unter `http://localhost:8080` erreichbar; außer dem Bildschirm selbst braucht jedes Gerät dafür einen Kopplungscode aus dem Menü („Am Handy“).

Für „Am Handy“ muss der Port im Heimnetz erreichbar sein. Dafür in einer `.env` neben `docker-compose.yml` eintragen und neu starten:

```bash
STORCHCAM_BIND=0.0.0.0
STORCHCAM_PUBLIC_URL=http://raspberrypi.local:8080
```

`STORCHCAM_PUBLIC_URL` ist die Adresse, unter der Handys den Pi erreichen (Hostname oder IP-Adresse, Port 8080); sie steht im QR-Code. Alternativ lässt sie sich im Menü unter „Am Handy“ eintragen.

Nach Codeänderungen immer mit `--build` starten, sonst läuft das alte Image weiter.

**Update von einer Version vor 1.0 (früherer Name BI-StorchCam):** Einstellungen in `./data` und eine vorhandene `.env` bleiben gültig, auch mit den alten Variablennamen `BI_STORCHCAM_…`. Vor dem Update den alten Container mit `docker compose down` stoppen (er hieß noch `bi-storchcam`), danach `docker compose up -d --build`. Wer Gaming nutzt, führt anschließend `sudo ./scripts/gaming/install-agent.sh` erneut aus; der Hilfsdienst übernimmt die bisherige Einrichtung und den vorhandenen Gaming-Container.

Voraussetzungen für die Anzeige auf dem Bildschirm:

- Desktop-Autologin für den Benutzer mit UID 1000 (Wayland-Socket `/run/user/1000/wayland-0`)
- Der Container bindet `/run/user` (Wayland, Audio) und `/dev/dri` (GPU) ein und läuft mit `seccomp=unconfined`, damit die Chromium-Sandbox aktiv bleiben kann.
- Startet Docker beim Booten vor der Desktop-Sitzung, wartet der Container bis zu fünf Minuten auf den Wayland-Socket.

Optionale Umgebungsvariablen (z. B. in einer `.env` neben `docker-compose.yml`):

| Variable | Wirkung |
| --- | --- |
| `STORCHCAM_STREAM_URL` | setzt bei jedem Start die Stream-URL |
| `YOUTUBE_API_KEY` | nutzt für die Live-Suche die offizielle YouTube-API statt der öffentlichen Suchseite |
| `STORCHCAM_KIOSK=0` | nur Server, kein Browser auf dem Bildschirm |
| `STORCHCAM_REQUIRE_PIN=1` + `STORCHCAM_ADMIN_PIN` | Menü wieder mit PIN schützen |
| `STORCHCAM_BIND=0.0.0.0` | Port 8080 im Heimnetz freigeben (für „Am Handy“); Standard nur `127.0.0.1` |
| `STORCHCAM_PORT` | Port am Pi, Standard `8080`; ändern, wenn ihn schon ein anderer Dienst belegt (z. B. Pi-hole) |
| `STORCHCAM_PUBLIC_URL` | Adresse des Pi im Heimnetz für den QR-Code, z. B. `http://raspberrypi.local:8080` |

Diagnoseoptionen laufen ebenfalls im Container, z. B. `docker compose run --rm storchcam --test-weather`.

Ohne Docker: `python -m pip install -e .` und `python -m storchcam --no-browser`.

## Menü

Der Knopf oben links (oder `Ctrl+Alt+S`) öffnet das Menü als Seitenpanel über etwa zwei Drittel der Breite; die Kamera bleibt daneben sichtbar und läuft weiter, auf Bildschirmen bis 900 Pixel Breite als kleines Bild in der Ecke. Alles ist für Finger gebaut: Tippflächen sind mindestens 48 Pixel groß und wachsen mit dem Bildschirm, Text lässt sich nicht versehentlich markieren, Auswahllisten und Uhrzeiten öffnen ein großes Auswahlblatt statt kleiner Dropdowns.

Die Startseite des Menüs zeigt oben, was gerade läuft (mit Zurück, Pause, Vor, Lautstärke und Ausschalt-Timer), darunter große Kacheln mit dem aktuellen Stand. Eine Kachel öffnet ihre Seite von rechts; zurück geht es mit „‹ Zurück“ oder einem Wisch vom linken Rand, ein Wisch nach links schließt das Menü. Jede Änderung wirkt und speichert sofort; ein Hinweis zeigt zehn Sekunden lang „Rückgängig“. Texteingaben laufen am Bildschirm über die eingebaute Bildschirmtastatur. Ein Wisch nach links schließt das Menü, Hinweise verschwinden beim Antippen.

- **Kamera:** was gerade läuft, Bildqualität, Bildrate (30 oder 60 Bilder pro Sekunde), Ton der Kamera (aus, leise und aus bei Musik, an), Favoriten mit Reihenfolge und automatischem Ersatz und die Suche nach Streams, die gerade auf YouTube live sind. Antippen spielt sofort, der Stern merkt den Stream als Favorit. Unter „Eigene Adresse“ lassen sich YouTube-Links, HLS-Adressen oder Webseiten eintragen.
- **Ort & Wetter:** weltweite Suche nach Adresse, Straße oder Ort (Nominatim/OpenStreetMap, Rückfall auf Open-Meteo). Antippen übernimmt Name, Koordinaten und Zeitzone; Uhr und Wetter folgen. Dazu die Stufe für Unwetterwarnungen.
- **Anzeige:** Theme mit Vorschau, Tag/Nacht für Y2K und Dagobert, Leseabstand, Blöcke der Infoleiste, Zeit bis zur Kompaktleiste und Nachtmodus.
- **ÖPNV:** Losgeh-Zeit an oder aus, je Haltestelle Gehzeit und Linienfilter; Haltestellen derzeit nur im VRR (Nordrhein-Westfalen).
- **Musik:** der Player mit Klangregler und Warteschlange; „Durchsuchen“ führt zu Radio, YouTube Music und Spotify mit Seiten für Alben, Playlists und Künstler. Unten bleibt ein Mini-Player, der zum Player zurückführt. „Konten und Lautsprecher“ hat Anmeldungen, Spotify auf dem Pi und Bluetooth für Lautsprecher und Controller.
- **System:** Zustand der Datenquellen (Zustand, letzte Antwort, Abfrageintervall), optionaler YouTube-API-Schlüssel, Start-Suchwort und „Einrichtung erneut starten“.
- **Gaming:** RetroArch als eigener Container, eingerichtet und verwaltet über das Menü (Hilfsdienst auf dem Pi, Speicherordner wählbar, Start/Stopp, Spiele hochladen, Update). Gespielt wird direkt am Pi-Bildschirm mit RetroArch im Vollbild und Controller am Pi (USB oder Bluetooth) oder im Browser von Laptop, Tablet oder Handy. Einmalige Einrichtung und Details in [docs/GAMING.md](docs/GAMING.md).
- **Am Handy:** QR-Code und Kopplungscode, Adresse des Pi im Heimnetz und ob ein Handy sich auch mit PIN anmelden darf.

## Adminzugriff außerhalb von Docker

Ohne Docker bleibt das Menü PIN-geschützt (`admin.require_pin`). Beim ersten Öffnen wird eine PIN mit 4 bis 12 Ziffern festgelegt; gespeichert wird nur ein PBKDF2-SHA256-Hash. Der Server bindet dann standardmäßig ausschließlich an `127.0.0.1`.

## Konfiguration und Logs

- Konfiguration: `./data/config.json` (im Container `/data/config.json`, Override per `STORCHCAM_CONFIG`)
- Letzte Sicherung: `./data/config.json.bak`
- App-Log: `./data/.cache/StorchCam/storchcam.log` bzw. `docker compose logs -f`

Eine ungültige Originaldatei wird nicht still überschrieben. Alle Felder sind in [docs/CONFIGURATION.md](docs/CONFIGURATION.md) beschrieben.

## Tests

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy storchcam
pytest -q
npm ci
npx playwright install chromium
npm run test:ui
```

Playwright verwendet gemockte API-Antworten und ruft keine echten externen Provider auf.

## Datenschutz und Netzwerk

StorchCam verarbeitet Standort, Stream-URL und Haltestellen lokal. Externe Anbieter erhalten technisch notwendige Netzwerkdaten wie IP-Adresse und angefragte Koordinaten beziehungsweise Haltestellen. Eine externe Serverbindung sollte nur in einem vertrauenswürdigen Netz und mit eingerichteter PIN verwendet werden.

## Datenquellen und Attribution

- Wetter: [Open-Meteo](https://open-meteo.com/)
- Adresssuche: [Nominatim](https://nominatim.org/), Daten © [OpenStreetMap-Mitwirkende](https://www.openstreetmap.org/copyright)
- ÖPNV: VRR-Haltestellenmonitor
- Unwetterwarnungen: [Deutscher Wetterdienst](https://www.dwd.de/) über [Bright Sky](https://brightsky.dev/)
- Radiosender: [Radio Browser](https://www.radio-browser.info/), Community-Datenbank

- Videoplayer: [hls.js](https://github.com/video-dev/hls.js) 1.7.3, Apache-2.0, liegt unter `storchcam/web/vendor/`

Marken und Logos werden nicht unnötig eingebunden.

## Lizenz

Der Quellcode steht unter [GPL-3.0-only](LICENSE). Daten unterliegen den Bedingungen der jeweiligen Anbieter.
