# Sicherheit

## Lücken melden

Bitte Sicherheitslücken nicht als öffentliches Issue melden, sondern vertraulich über GitHub:
Reiter **Security** → **Report a vulnerability**. Hilfreich sind betroffene Version, Schritte zum
Nachstellen und die mögliche Auswirkung.

## Unterstützte Versionen

Sicherheitskorrekturen gibt es für die jeweils neueste Version.

## Sicherheitsmodell in Kürze

- StorchCam ist für das eigene Heimnetz gedacht. Den Port nie im Router ins Internet freigeben.
- Standardmäßig lauscht der Container nur auf `127.0.0.1`. Mit `STORCHCAM_BIND=0.0.0.0` brauchen
  andere Geräte einen einmaligen Kopplungscode aus dem Menü oder die PIN.
- Außerhalb von Docker ist das Menü per PIN geschützt; gespeichert wird nur ein PBKDF2-SHA256-Hash.
- Der Gaming-Hilfsdienst lauscht nur auf einem Unix-Socket, kennt eine feste Liste von Aktionen und
  verwaltet ausschließlich den Gaming-Container. Der Docker-Socket wird nicht in StorchCam eingebunden.
- Für das Spielen am Pi-Bildschirm startet der Hilfsdienst nur RetroArch aus den Systempaketen, als
  angemeldeter Benutzer und mit festen Argumenten. Pakete installiert nur `install-agent.sh --screen` nach
  Rückfrage.
- Die Gaming-Oberfläche (RetroArch über Selkies) ist mit Benutzer und zufälligem Passwort geschützt und
  enthält laut Image-Dokumentation ein Terminal im Container; auch sie gehört nur ins Heimnetz.
