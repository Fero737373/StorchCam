# Konfigurationsreferenz

Die JSON-Konfiguration wird gegen ein geschlossenes Schema validiert. Unbekannte Felder und unsichere Werte werden abgelehnt.

| Pfad | Bedeutung | Bereich/Standard |
| --- | --- | --- |
| `app.timezone` | Zeitzone für Prognosen | IANA-Name, `Europe/Berlin` |
| `app.cache_dir` | rotierende Logs | `~/.cache/StorchCam` |
| `server.host` | lokale Bind-Adresse | `127.0.0.1` |
| `server.port` | HTTP-Port | 1024–65535, `8000` |
| `server.max_request_bytes` | maximale JSON-Größe | 4 KiB–1 MiB |
| `server.admin_session_minutes` | Token-Laufzeit, auch für gekoppelte Handys | 5–240 Minuten |
| `server.public_url` | Adresse, unter der Handys im Heimnetz den Pi erreichen (QR-Code bei „Am Handy“); leer = `STORCHCAM_PUBLIC_URL` bzw. außerhalb von Docker die eigene LAN-Adresse | leer oder `http(s)://…` |
| `server.lan_access` | Geräte im Heimnetz dürfen sich auch mit PIN anmelden (sonst nur mit einem frischen Kopplungscode); braucht eine PIN | `false` |
| `kiosk.enabled` | verwalteten Browser starten | `true` |
| `kiosk.browser` | Browserwahl | `auto`, `chromium`, `chrome`, `edge` oder absoluter Pfad |
| `kiosk.profile_dir` | eigenes Browserprofil | leer = plattformsicherer Standard |
| `kiosk.disable_screensaver` | X11-Bildschirmschoner abschalten | `true` |
| `kiosk.use_gpu` | Hardwarebeschleunigung erlauben | `true` |
| `kiosk.browser_restart_seconds` | erster Backoff | 1–60 Sekunden |
| `kiosk.browser_restart_max_seconds` | maximaler Backoff | 5–600 Sekunden |
| `kiosk.browser_stable_seconds` | Laufzeit bis Zähler-Reset | 10–3600 Sekunden |
| `kiosk.browser_max_failures` | Abbruch der Restartschleife | 1–50 |
| `kiosk.log_file` | Browserlog | `~/.cache/StorchCam/chromium.log` |
| `kiosk.log_max_bytes` / `log_backups` | Browserlogrotation | 2 MiB / 3 |
| `kiosk.extra_flags` | zusätzliche Chromium-Flags | Textliste |
| `screen.hardware_profile` | Hardwareverhalten | `generic`, `raspberry_pi_dsi_portrait`, `raspberry_pi_dsi_landscape`, `custom` |
| `screen.output` | X11-Ausgang | `auto` oder exakter Name |
| `screen.rotation` | X11-Rotation | `none`, `normal`, `left`, `right`, `inverted` |
| `screen.touch_device` | exakter XInput-Name | leer = keine Änderung |
| `screen.touch_matrix` | Transformation | leer = keine Änderung |
| `location.label` | sichtbarer Standort | maximal 120 Zeichen |
| `location.latitude` / `longitude` | Wetterposition | −90…90 / −180…180 |
| `stream.url` | Stream-Adresse. YouTube-Links (werden in die Embed-Form umgewandelt) und `.m3u8`-Adressen spielt der eigene Player, alles andere wird eingebettet | leer oder vollständiges HTTP(S) |
| `stream.quality` | Höchste Bildhöhe im eigenen Player | `auto` (= 1080), `1080`, `720`, `480` |
| `stream.max_fps` | Höchste Bildrate; 60 braucht deutlich mehr Leistung, weil der Pi das Video meist in Software entschlüsselt | `30`, `60` |
| `stream.audio` | Ton der Kamera: aus, leise und während Musik aus (`duck`), an. `stream.muted` folgt automatisch | `off`, `duck`, `on` |
| `stream.favorites` | gemerkte Kameras `{url, title, channel}`; die Reihenfolge bestimmt den Ersatz bei Ausfall | bis 12 |
| `stream.auto_fallback` | bei Ausfall zum nächsten Favoriten wechseln und zurück, sobald die eigentliche Kamera wieder läuft | `true` |
| `stream.autoplay` | Playerparameter für eingebettete Seiten | boolesch |
| `admin.pin_hash` | lokaler PBKDF2-Hash | nur durch Setup setzen |
| `admin.require_pin` | Menü nur mit PIN öffnen; im Docker-Container per `STORCHCAM_REQUIRE_PIN` gesetzt | `true`, Docker: `false` |
| `youtube.api_key` | optionaler Schlüssel für die YouTube Data API v3; wird nie an die Oberfläche ausgeliefert, leer = öffentliche Suchseite oder `YOUTUBE_API_KEY` | leer |
| `youtube.default_query` | Suchwort beim Öffnen der Livestream-Auswahl | `Storch` |
| `ui.theme` | Farbsystem; `auto` ist am Tag hell und nachts dunkel; `y2k` zeigt tagsüber ziehende Wolken, nachts funkelnde Sterne; `dagobert` ist ein Comic-Geldspeicher mit fallenden Talern am Tag und Sternenhimmel bei Nacht; `flap` zeigt Uhr und Minuten als Fallblatt; `paper` ist ruhig wie E-Ink; `plasma` ist dunkles Industrieblau mit wanderndem Lichtstrahl | `auto`, `dark`, `light`, `high-contrast`, `y2k`, `dagobert`, `flap`, `paper`, `plasma` |
| `ui.sky` | Tag/Nacht der Themes `y2k` und `dagobert`: automatisch nach Sonnenstand (Open-Meteo) oder fest | `auto`, `day`, `night` |
| `music.source` | Quelle für „Läuft gerade“ oben rechts | `off`, `auto` (Spotify, sonst YouTube Music), `spotify`, `ytmusic` (Anmeldung im Menü unter Musik, siehe API_NOTES) |
| `music.spotify.client_id` | Client-ID der eigenen Spotify-App (Redirect URI: `http://127.0.0.1:<Port>/api/music/spotify/callback`) | Text |
| `music.radio.favorites` | Lieblingssender `{uuid, name, url, country, codec, bitrate, tags}` aus der Radio-Browser-Suche; Vor und Zurück wechseln in dieser Reihenfolge | bis 30 |
| `music.equalizer` | Klangregler für alles, was mpv auf dem Pi spielt: `preset` (`flat`, `bass`, `pop`, `rock`, `klassik`, `sprache`, `akustik`, `nacht` oder `custom`) und `gains` in dB für 60 Hz, 150 Hz, 400 Hz, 1 kHz, 2,4 kHz und 15 kHz; der größte Anhub wird vorab abgezogen, damit nichts übersteuert | je Band -12 bis +12, Vorgabe `flat` |
| `gaming.pause_camera` | Kamerabild am Pi anhalten, solange der Gaming-Container läuft (mehr Leistung fürs Spiel) | `false` |
| `ui.distance` | Leseabstand zum Bildschirm in Metern; skaliert die Infoleiste (1 m kleiner, 3 m größer) | `1`, `2`, `3` |
| `ui.layout_profile` | früheres Layoutprofil, nur noch für alte Konfigurationen; beim Umstieg auf Version 5 wird daraus `ui.distance` | `auto`, `minimal`, `standard`, `information` |
| `ui.compact_after` | Infoleiste nach so vielen Sekunden ohne Berührung zur Kompaktleiste verkleinern; `0` = nie | 0–3600, `120` |
| `ui.setup_done` | Einrichtung beim ersten Start erledigt; ohne Stream und mit `false` öffnet sich der Assistent | boolesch |
| `ui.night.enabled` / `start` / `end` | Nachtmodus mit Zeitfenster (auch über Mitternacht) | `false`, `23:00`, `06:00` |
| `ui.night.screen_off` | nachts die Hintergrundbeleuchtung ausschalten statt nur zu dimmen; wirkt nur, wo `/sys/class/backlight/*/bl_power` beschreibbar ist | `false` |
| `ui.*.enabled` | Uhr/Wetter/ÖPNV/System in der Infoleiste; ausgeschaltete Blöcke entfallen samt Trenner | boolesch |
| `weather.provider` | Wetterprovider | derzeit `openmeteo` |
| `weather.refresh_seconds` | Wetterintervall | 60–3600 Sekunden |
| `weather.forecast_hours` | Zukunftsfenster | 1–48 Stunden |
| `weather.rain_mm_threshold` | Regenmenge-Grenze | 0–100 mm |
| `weather.rain_probability_threshold` | Wahrscheinlichkeitsgrenze; beide Schwellen bestimmen die Regenzusammenfassung („Regen ab …“), die Wahrscheinlichkeit auch die gestrichelte Linie im Wetterstreifen | 0–100 % |
| `weather.warnings` | amtliche DWD-Warnungen über Bright Sky ab dieser Stufe (nur Deutschland) | `off`, `minor`, `moderate`, `severe`; `moderate` |
| `transit.provider` | ÖPNV-Provider | derzeit `vrr` |
| `transit.refresh_seconds` | Abfahrtsintervall | 15–3600 Sekunden |
| `transit.default_max_rows` | Standardzeilen | 1–12 |
| `transit.target_len` | Ziellängenkürzung | 8–80 Zeichen |
| `transit.leave_mode` | Losgeh-Zeit (Abfahrt minus Gehzeit der Haltestelle) statt Abfahrtszeit | `true` |
| `transit.stops` | bis zu 20 Haltestellen | Liste, siehe unten |
| `logging.level` | App-Loglevel | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logging.max_bytes` / `backups` | App-Logrotation | 64 KiB–100 MiB / 1–20 |

Eine Haltestelle enthält `title`, `station_name`, `station_id`, `line_filter`, `nightbus_only`, `hide_if_empty`, `max_rows` und `walk_minutes` (Gehzeit zur Haltestelle, 0–30 Minuten; 0 zeigt die Abfahrtszeit). Die Oberfläche verwaltet diese Felder ohne manuelle JSON-Bearbeitung. Der VRR-Provider gilt nur für seine unterstützte Region.

Die Infoleiste zeigt höchstens drei Abfahrten (unter 760 px Breite zwei) über alle Haltestellen hinweg, nach Losgeh-Zeit bzw. Minuten sortiert; nicht mehr erreichbare Abfahrten werden blass, „Jetzt los“ hebt die nächste hervor. `max_rows` begrenzt weiterhin, wie viele Zeilen je Haltestelle abgefragt werden.

Im Theme `dagobert` steht eine Comicfigur auf der Infoleiste (tagsüber mit Taler, nachts mit Schlafmütze). Eigene Bilder ersetzen die mitgelieferte Zeichnung: `dagobert-tag` und `dagobert-nacht` als `.png`, `.webp` oder `.svg` in den Ordner `theme/` neben der `config.json` legen (Docker: `data/theme/`). Am besten eignen sich freigestellte Bilder mit transparentem Hintergrund.

Das frühere Regenradar wurde entfernt. Alte Konfigurationen mit `ui.radar` oder `radar` werden beim Start automatisch bereinigt.

Wiedergabeverlauf („Weiterhören“) liegt in `history.json` neben der `config.json`; er ist kein Teil der Konfiguration und kann gelöscht werden.

Konfigurationen der Version 4 werden beim Start auf Version 5 gehoben: `stream.muted: false` wird zu `stream.audio: "on"`, das Layoutprofil zu `ui.distance` (`minimal` → 1, `information` → 3, sonst 2), und mit eingetragenem Stream gilt die Einrichtung als erledigt.
