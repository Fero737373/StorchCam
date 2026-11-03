# Provider- und API-Hinweise

## Lokale HTTP-API

- `GET /api/health`: Version, Uptime und State-Readiness
- `GET /api/state`: letzter In-Memory-Snapshot ohne neue Providerabfrage
- `GET /api/admin/status`: PIN-/Sessionstatus, dazu `remote` (Anfrage kommt nicht vom Bildschirm selbst) und `pin_login` (PIN-Anmeldung erlaubt)
- `POST /api/admin/setup`: einmaliges lokales PIN-Setup
- `POST /api/admin/login`: zeitlich begrenzte Admin-Session
- `GET /api/config`: validierte, redigierte Konfiguration; nach Setup authentifiziert
- `POST /api/config/save`: authentifizierter, größenbegrenzter Schreibzugriff
- `GET /api/station/search`: authentifizierte VRR-Suche
- `GET /api/live/search?q=`: Live-Streams auf YouTube
- `GET /api/stream/source`: wie der Kiosk den Stream abspielt: `{kind: "hls"|"file", url, title, channel, live, expires_in}` für den eigenen Player (`file` = normales Video in Schleife), `{kind: "embed", reason}` oder `{kind: "none"}`. `refresh=1` holt eine frische Adresse; `url=` probiert eine andere Adresse aus: Favoriten darf der Kiosk selbst (Ersatz bei Ausfall), alles andere nur das Menü
- `GET /api/stream/hls/playlist?u=&s=` und `GET /api/stream/hls/segment?u=&s=`: reichen Playlists und Segmente der Quelle durch. Nur Adressen mit gültiger HMAC-Signatur aus einer zuvor ausgelieferten Playlist werden abgerufen; der Schlüssel entsteht bei jedem Start neu. Das Segment-Endpunkt reicht `Range` weiter (offene Bereiche in 10-MB-Stücken) und antwortet mit 206, damit Videodateien springen und in Schleife laufen
- `GET /api/location/search?q=`: Adress- und Ortssuche weltweit
- `GET /api/location/timezone?lat=&lon=`: Zeitzone zu Koordinaten
- `POST /api/music/spotify/authorize` (Admin): startet die Spotify-Anmeldung (Authorization Code mit PKCE) und liefert die Login-Adresse
- `GET /api/music/spotify/callback`: Rücksprung von Spotify, geschützt über den einmaligen `state`; leitet auf `/?music=connected` bzw. `/?music=error` um
- `POST /api/music/spotify/disconnect` (Admin): löscht den gespeicherten Spotify-Zugang
- `POST /api/music/ytmusic/login` (Admin): öffnet auf dem angeschlossenen Bildschirm ein Chromium-Fenster mit der Google-Anmeldung für YouTube Music
- `GET /api/music/ytmusic/login` (Admin): Stand der Anmeldung (`idle`, `pending`, `connected`, `error`)
- `POST /api/music/ytmusic/cancel` (Admin): schließt das Anmeldefenster
- `POST /api/music/ytmusic/disconnect` (Admin): löscht den gespeicherten YouTube-Music-Zugang
- `POST /api/player/search` (Admin): `{service: "ytmusic"|"spotify", query}` → Treffer mit `kind` (`song`, `video`, `artist`, `album`, `playlist`), `id`, `title`, `subtitle`, `cover`, `duration`
- `POST /api/player/open` (Admin): `{service, kind: "album"|"playlist"|"artist", id}` → Seite mit `title`, `subtitle`, `cover`, `tracks` (`id`, `title`, `artist`, `cover`, `duration`, `liked`) und bei Künstlern `albums` sowie `play_id` (YouTube Music: Playlist mit allen Songs des Künstlers)
- `POST /api/player/like` (Admin): `{service, id, liked}` setzt „Gefällt mir“ (YouTube Music `rate_song`, Spotify `/me/tracks`)
- `POST /api/player/add` (Admin): `{service, playlist, id}` fügt den Titel einer eigenen Playlist hinzu; „Liked Music“ füllt sich nur über „Gefällt mir“
- `GET /api/player/queue` (Admin; vom Pi selbst ohne Anmeldung): Spotify-Warteschlange zum Ansehen; YouTube Music und Radio stehen direkt im Musik-Zustand
- `GET /api/player/level` (Admin; vom Pi selbst ohne Anmeldung): Pegel 0 bis 1 des Pi-Players für die bewegte Equalizer-Anzeige
- `POST /api/player/playlists` (Admin): `{service}` → eigene Playlists (YouTube-Music-Bibliothek inkl. „Liked Music“ bzw. Spotify `/me/playlists`), Format wie bei der Suche
- `POST /api/player/play` (Admin): `{service, kind, id, start?, shuffle?, title?, subtitle?, cover?}` startet die Wiedergabe und merkt sie für „Weiterhören“. Ein Song oder Video spielt allein; Album, Playlist und Künstler spielen als Warteschlange ab `start`, auf Wunsch gemischt. Es spielt immer nur ein Dienst; beim Pi-Player kommt der neue Musik-Zustand gleich als `music` zurück
- `POST /api/radio/search` (Admin): `{query, filter}` → Sender aus Radio Browser (`filter`: `news`, `pop`, `rock`, `jazz`, `klassik`, `de`; ohne Suchwort die beliebtesten)
- `POST /api/radio/play` (Admin): `{station}` spielt den Sender über mpv auf dem Pi, meldet das Abspielen an Radio Browser und merkt ihn für „Weiterhören“
- `POST /api/player/sleep` (Admin; vom Bildschirm selbst auch ohne Anmeldung): `{minutes, label}` schaltet die Musik nach der Zeit aus, `0` hebt den Timer auf; das Ende steht als `sleep_end` (ms) im Musik-Zustand
- `GET /api/music/recent` (Admin): zuletzt gehört über alle Quellen
- `POST /api/pair/start`, `GET /api/pair/status`, `POST /api/pair/cancel` (Admin): Kopplungscode fürs Handy (6 Ziffern, 10 Minuten, einmalig) mit Link und QR-Code als SVG-Daten-URI
- `POST /api/pair/claim` (ohne Anmeldung): `{code}` → Sitzung wie nach einer PIN-Anmeldung; nach fünf falschen Versuchen verfällt der Code
- `GET /api/system/info` (Admin): Version, Laufzeit, letzte Sicherung der Konfiguration, ob die Hintergrundbeleuchtung schaltbar ist, Adresse im Heimnetz
- `POST /api/display/power` (nur vom Bildschirm selbst): `{on}` schaltet die Hintergrundbeleuchtung für den Nachtmodus
- `POST /api/player/control` (Admin; vom Pi selbst auch ohne Anmeldung für die Knöpfe der Songkarte): `{target: "pi"|"spotify", action, value}` mit `toggle`, `next`, `previous`, `stop`, `volume` (0–100), `seek` (Sekunden), `shuffle` (`true`/`false`), `repeat` (`off`, `all`, `one`) und nur beim Pi-Player `jump` (Position), `remove` (Position, nicht der laufende Titel) und `move` (`{from, to}`). Beim Radio wechseln `next` und `previous` den Lieblingssender. Für `pi` antwortet der Server mit dem neuen Musik-Zustand als `music`
- `GET /api/music/spotify/pair`, `POST /api/music/spotify/pair`, `POST /api/music/spotify/unpair` (Admin): Pi als Spotify-Lautsprecher koppeln (Kopplungsadresse und QR-Code) bzw. die Kopplung löschen
- `GET /api/bluetooth` (Admin): Adapter, Audiogeräte und laufende Aktion; ohne Adapter `available: false` mit Fehlertext
- `POST /api/bluetooth/scan`, `/connect`, `/disconnect`, `/remove` (Admin): `{mac}`; laufen im Hintergrund, der Fortschritt steht in `GET /api/bluetooth`

Gaming (alle mit Admin-Sitzung; StorchCam reicht nur an den Gaming-Hilfsdienst auf dem Pi weiter, siehe docs/GAMING.md):
- `GET /api/gaming/status`: `agent` (Hilfsdienst erreichbar), `docker` (`ok`, `version`, `error`), `container` (Name, Zustand, Image, Version, Port, `reachable`, Mounts) oder `candidates` (vorhandene RetroArch-Container), laufende `job`, `setup` und `settings.pause_camera`; ohne Hilfsdienst `install_command`
- `GET /api/gaming/folders?path=`: freigegebene Ordner bzw. Unterordner mit freiem Speicher und Schreibrechten; `POST /api/gaming/folders` `{path, name}` legt einen Ordner an
- `GET /api/gaming/ports?port=`: ob der Port auf dem Pi frei ist
- `POST /api/gaming/install` `{folder, port, gpu?, confirm: true}`: startet die Installation; `GET /api/gaming/job` zeigt die Schritte (`pending`, `running`, `done`, `error`, `skipped`)
- `POST /api/gaming/control` `{action: "start"|"stop"}`; `POST /api/gaming/update` `{rollback?, confirm: true}`; `POST /api/gaming/adopt` `{name, confirm: true}`
- `GET /api/gaming/access`: Adresse (`https://<Pi>:<Port>/`), Benutzer, Passwort und QR-Code
- `GET /api/gaming/screen`: RetroArch am Pi-Bildschirm (`installed`, `cores`, `display`, `running`, `last_exit`, `install_command`); steht auch in `GET /api/gaming/status` unter `screen`. `POST /api/gaming/screen` `{action: "start"|"stop"}` startet bzw. beendet es
- `GET /api/gaming/roms`: Spieldateien mit Konsole; `PUT /api/gaming/roms?name=` lädt eine Datei hoch (Rohdaten, bis 4 GB)

`GET /api/state` enthält neben Wetter (`hourly` für 25 Stunden ab der laufenden Stunde, `sun` mit Auf- und Untergang), Abfahrten (`delay`, `type` = `bus`/`tram`/`night`/`train`, `at`/`planned` als Unix-Zeit, `walk_minutes` je Haltestelle) und Musik (beim Pi-Player mit `track_id`, `liked`, `shuffle`, `repeat`, `queue` als Ausschnitt mit `position`, `queue_index`, `seekable` und `equalizer`; beim Radio `recent_titles` und das Cover des laufenden Songs) auch `warnings` (DWD) und `health` (je Quelle `ok`, `checked_at`, `last_ok`, `error`, `interval`) für die Übersicht im Menü.

Zugriff: Der Bildschirm selbst (Loopback) darf ohne PIN alles, wenn keine PIN verlangt ist. Andere Geräte brauchen immer eine Sitzung, entweder über einen Kopplungscode vom Bildschirm oder, wenn `server.lan_access` an ist, über die PIN.

Fehler werden als JSON mit passenden HTTP-Statuscodes ausgegeben. Tracebacks werden nicht an den Browser übertragen.

## Externe Provider

- Open-Meteo: aktuelles Wetter und stündliche Zukunft ab der konfigurierten Zeitzone
- Nominatim (OpenStreetMap): Adress- und Ortssuche, Rückfall auf Open-Meteo-Geocoding
- YouTube: Live-Suche über die öffentliche Suchseite oder optional die Data API v3
- YouTube-Livestream im eigenen Player: yt-dlp liefert die HLS-Master-Playlist (googlevideo, gültig einige Stunden, Ablauf steht in der Adresse). Der Proxy entfernt Varianten über `stream.quality` und über `stream.max_fps` (Standard 30 Bilder pro Sekunde), hls.js wählt aus dem Rest passend zur Leitung. Der Player holt zehn Minuten vor Ablauf eine neue Adresse, startet ein pausiertes Video sofort wieder und baut die Verbindung nach 20 Sekunden ohne neues Bild neu auf; nach fünf Fehlversuchen wechselt er zum nächsten Favoriten (wenn `stream.auto_fallback` an ist und Favoriten gemerkt sind), sonst auf die Einbettung
- VRR-Haltestellenmonitor: Suche und Abfahrten in der VRR-Region; je Abfahrt Verkehrsmittel (`type`), Verspätung (`delay`) und Plan- bzw. erwartete Zeit (`orgFullTime`/`fullTime`)
- Bright Sky (`/alerts?lat=&lon=`): amtliche Warnungen des Deutschen Wetterdienstes für die Gemeinde des Orts, alle zehn Minuten; außerhalb Deutschlands antwortet die API mit 404, dann zeigt der Kiosk nichts
- Radio Browser: Sendersuche über einen per DNS (`all.api.radio-browser.info`) gefundenen Server, Rückfall auf feste Server; jedes Abspielen wird wie gewünscht über `/json/url/<uuid>` gemeldet. mpv spielt die Senderadresse direkt, der Titel kommt aus den ICY-Metadaten des Streams; reißt der Stream ab, verbindet mpv bis zu fünfmal neu
- YouTube-Videos ohne HLS (kein Livestream): yt-dlp liefert die Formatliste, die App nimmt die beste einzelne Videodatei in H.264 (sonst VP9) bis zur eingestellten Höhe und Bildrate; der Kiosk spielt sie stumm in Schleife
- Spotify Web API: aktueller Song (`/me/player/currently-playing`), Suche, Wiedergabe und Steuerung (`/me/player/...`); der Refresh-Token liegt in `spotify_token.json` neben der `config.json` (Dateirechte 600). Für Steuerung und eigene Playlists braucht die Verbindung die Rechte `user-read-playback-state`, `user-modify-playback-state`, `playlist-read-private` und `playlist-read-collaborative`; ältere Verbindungen im Menü einmal neu verbinden
- Spotify auf dem Pi: librespot 0.8 (aus raspotify 0.48.3) läuft als Spotify-Connect-Lautsprecher „StorchCam“ mit PulseAudio-Ausgabe. Gekoppelt wird per Gerätecode (`--enable-device-auth`, Adresse spotify.com/pair); die Zugangsdaten liegen in `librespot/` neben der `config.json`. Abgespielt wird über die Web API mit `device_id` dieses Lautsprechers
- YouTube Music auf dem Pi: mpv (JSON-IPC) spielt die Audio-Streams, die yt-dlp mit den YouTube-Music-Cookies auflöst; yt-dlp braucht dafür die JavaScript-Laufzeit Deno. Ein Song startet das passende Radio, Alben und Playlists laufen als Warteschlange
- Bluetooth: `bluetoothctl` spricht über den eingebundenen System-D-Bus (`/run/dbus/system_bus_socket`) mit BlueZ auf dem Host. PipeWire auf dem Host nimmt einen verbundenen Lautsprecher selbst als Ausgabe. Weil PipeWire Bluetooth-Lautsprecher oft mit 40 % (rund -24 dB) startet, stellt die App die Ausgabe nach dem Verbinden und bei jedem Abspielen per `pactl` auf 100 %; leiser gestellt wird im Player
- YouTube Music (inoffizielle Bibliothek ytmusicapi): Google sperrt die Geräteanmeldung (OAuth) für YouTube Music seit 2025 und verweigert Anmeldungen in ferngesteuerten Browsern. Deshalb öffnet die App ein gewöhnliches Chromium-Fenster ohne Debug-Zugang (Profil `ytmusic-browser/` neben der `config.json`) mit der normalen Google-Anmeldung. Sie erkennt die Anmeldung am Namen des Sitzungscookies in der Cookie-Datenbank, schließt das Fenster und liest die youtube.com-Cookies danach mit einem Headless-Chromium über `--remote-debugging-pipe` aus. Die Cookies liegen in `ytmusic_auth.json` (Dateirechte 600). Das Profil bleibt bis „YouTube Music trennen“ erhalten, sodass eine erneute Anmeldung meist ohne Passwort durchläuft. Es gibt keine „läuft gerade“-Schnittstelle: Der neueste Eintrag im Wiedergabeverlauf gilt ab seinem Erscheinen für seine Länge plus 30 s als laufend. Pause wird nicht erkannt, und der erste Song nach einem Neustart wird nicht angezeigt

Providerfehler bleiben im Snapshot sichtbar, beenden aber weder Server noch State-Worker.
