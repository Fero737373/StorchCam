# Architektur

StorchCam besitzt genau einen Produktpfad:

1. `kiosk_app.main` lädt und validiert die lokale Konfiguration.
2. `StateManager` aktualisiert Wetter, VRR, DWD-Warnungen, Musik und optionale Systemdaten nach getrennten Intervallen und führt je Quelle einen Zustand (`health`) für die Übersicht im Menü.
3. `StorchServer` bindet den konfigurierten Port und liefert nur unveränderliche State-Snapshots an `/api/state`.
4. Der Hauptprozess wartet auf `/api/health` mit `state_ready=true`.
5. Erst danach startet `BrowserManager` einen Chromium-Familienprozess.
6. Docker (`restart: unless-stopped`, Healthcheck auf `/api/health`) überwacht den Python-Prozess. `BrowserManager` überwacht den Chromium-Prozess im Container, der über den Wayland-Socket des Hosts auf dem Bildschirm erscheint.

Der Livestream läuft im eigenen Player: `/api/stream/source` löst YouTube per yt-dlp in eine HLS-Playlist (Livestream) oder eine einzelne Videodatei (normales Video) auf, `hls_proxy` reicht Playlist, Segmente und Videodateien signiert über den lokalen Server durch, hls.js bzw. das video-Element spielen sie ab. Chromium läuft mit Schaltern, die Medien im Hintergrund oder unter anderen Fenstern nicht anhalten. Fällt eine Kamera aus, wechselt die Oberfläche selbst zum nächsten Favoriten und prüft alle paar Minuten, ob die eigentliche Kamera wieder läuft.

Die Oberfläche (`web/index.html`, `app.js`, `style.css`) ist eine einzige Seite ohne Build-Schritt: Kiosk mit Infoleiste, Kompaktleiste, Detailansichten und Nachtmodus, darüber das Menü als Seitenpanel. Jede Einstellung speichert sofort über `/api/config/save` (mit Rückgängig), Themes sind reine CSS-Variablen auf `body[data-theme]`. Die strenge CSP (`'self'`, keine Inline-Styles) bleibt erhalten: Farben kommen über Klassen und SVG-Attribute, Bewegungen über die Web Animations API.

Das Menü ist ein Seitenstapel wie in einer iPhone-App: eine Startseite mit Kacheln, darüber geschobene Seiten (`goTo`, `pushPage`, `popPage` in `app.js`), jede mit eigener Scrollfläche. Album-, Playlist- und Künstlerseiten entstehen beim Öffnen und verschwinden beim Zurückgehen. Bewegt werden nur Lage und Deckkraft; Glas mit Unschärfe gibt es über dem Video nicht mehr, weil es jedes Videobild neu berechnen müsste. Größen hängen an `--tap` (mindestens 48 px, wächst mit `vmin`), damit die Bedienung auf jedem Bildschirm zur Fingerkuppe passt. Auswahllisten und Uhrzeiten bleiben als `select` bzw. `input` im Dokument, angezeigt wird aber ein großes Auswahlblatt.

Musik reagiert ohne Verzögerung: Jeder Tipp zeigt sofort den erwarteten Zustand (Pause, nächster Titel aus der Warteschlange, Zufall, ein neuer Song mit „lädt …“), der Pi-Player antwortet gleich mit seinem echten Zustand, und solange Musik läuft, wird der Zustand alle zwei Sekunden abgefragt. Im Hintergrund löst ein einziger Arbeitsthread (`Prefetcher`) die nächsten Titel und die ersten Songs einer geöffneten Seite vorab auf; `StreamResolver` startet yt-dlp für dieselbe ID nie doppelt, und geöffnete Seiten bleiben ein paar Minuten gemerkt (`PageCache`).

Musik auf dem Pi läuft über ein einziges mpv im Leerlauf. `player.py` ist eine Fassade vor `PiPlayer`; die Regeln der Warteschlange (Zufall, Wiederholen, Springen, Verschieben) stecken ohne Ein- und Ausgabe in `music_queue.PlayQueue`, der Klangregler in `equalizer.Equalizer` (mpv-Filterkette mit sechs `equalizer`-Bändern und einer `astats`-Pegelmessung für die Anzeige). `library.py` kapselt YouTube Music und Spotify als Strategien hinter derselben Schnittstelle `MusicLibrary` (Suche, Playlists, Seiten für Album, Playlist und Künstler, Gefällt mir, Zu Playlist hinzufügen); der Server wählt die Strategie je Anfrage. Radio läuft als Senderadresse mit ICY-Titel, `RadioCovers` sucht dazu das Cover über die iTunes-Suche. `listening.py` führt den Verlauf („Weiterhören“) und den Ausschalt-Timer. `pairing.py` vergibt den Kopplungscode fürs Handy; Geräte im Heimnetz bekommen nur damit oder mit PIN eine Sitzung.

Gaming verwaltet StorchCam nicht selbst: Der Container hat keinen Docker-Socket. Ein kleiner Dienst auf dem Pi (`gaming_agent.py`, systemd) lauscht auf einem Unix-Socket, der in den Container eingebunden ist, und kennt nur eine feste Liste von Aktionen für genau einen Gaming-Container (`lscr.io/linuxserver/retroarch`). `gaming.py` ist der Client dazu, die Endpunkte unter `/api/gaming/` reichen nur weiter. Für das Spielen am Pi-Bildschirm startet derselbe Dienst RetroArch aus den Systempaketen direkt auf dem Wayland-Desktop (`Screen` in `gaming_agent.py`), mit den Ordnern des Gaming-Containers für Spiele und Spielstände. Details in docs/GAMING.md.

Die API lädt außer der Streamauflösung keine externen Provider pro Request. Configänderungen invalidieren den Zeitplan des State-Managers. Schreibzugriffe erfordern ein kurzlebiges In-Memory-Token. Die PIN liegt nur als PBKDF2-Hash in der lokalen Config.

Provider bleiben hinter serialisierbaren Ergebnissen gekapselt. Neue Anbieter müssen die bestehende Configvalidierung, einen klaren Providernamen und einen Fetchpfad ergänzen; UI und HTTP-State benötigen dadurch keinen grundlegenden Umbau.
