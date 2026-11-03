# UI-Vorschauen

Klickbare Entwürfe für die Oberfläche, mit Beispieldaten und gezeichneten Kamerabildern. Sie verändern die App nicht; sie zeigen, wie sie nach den Verbesserungen aussehen und sich bedienen lassen soll. Zum Ansehen die Datei im Browser öffnen.

| Datei | Inhalt |
| --- | --- |
| [live-cam-kiosk.html](live-cam-kiosk.html) | Vollständige Vorschau: eigener Videoplayer statt YouTube-Fenster, Infoleiste mit Wetterstreifen und Losgeh-Zeit, Seitenmenü, Musik mit Radio, YouTube Music und Spotify, neun Themes, Nachtmodus, Einrichtung beim ersten Start. Oben in der Mitte schaltet „Vorschau“ Szenarien und Themes um und erklärt jede nummerierte Änderung. |
| [erste-verbesserungen.html](erste-verbesserungen.html) | Erste, behutsame Runde: gleiche Optik wie heute, zwölf gezielte Verbesserungen an Infoleiste und Menü. |

Die Seiten laden die Barlow-Schriften aus `storchcam/web/fonts/` und den QR-Code-Generator von cdnjs; ohne Internet fehlt nur der QR-Code.

## Umsetzung in der App

- **Videoplayer:** Der Server ermittelt mit yt-dlp die HLS-Adresse des Livestreams und reicht sie über einen lokalen Endpunkt durch; hls.js spielt sie in einem `<video>` ohne Bedienelemente. Ein Wächter startet das Video neu, falls es pausiert, und holt nach Ablauf der Adresse eine neue. Chromium-Schalter: `--autoplay-policy=no-user-gesture-required`, `--disable-background-media-suspend`, `--disable-backgrounding-occluded-windows`.
- **Radio:** Sendersuche über die Radio-Browser-API (`/json/stations/search`), Wiedergabe mit mpv, aktueller Titel aus den ICY-Metadaten des Streams.
- **Animationen:** im Stil von Uiverse.io selbst umgesetzt, nur `transform` und `opacity`, bei „reduzierte Bewegung“ aus.
