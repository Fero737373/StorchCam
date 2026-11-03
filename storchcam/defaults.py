"""Safe production defaults for the one supported web-kiosk application."""

CONFIG_SCHEMA_VERSION = 5

DEFAULT_CONFIG = {
    "app": {
        "name": "StorchCam",
        "language": "de",
        "timezone": "Europe/Berlin",
        "cache_dir": "~/.cache/StorchCam",
        "config_schema_version": CONFIG_SCHEMA_VERSION,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8000,
        "max_request_bytes": 262144,
        "admin_session_minutes": 30,
        # Adresse, unter der Handys im Heimnetz den Pi erreichen (für den QR-Code); leer = automatisch.
        "public_url": "",
        # Mit PIN auch ohne neuen Kopplungscode vom Handy aus anmelden.
        "lan_access": False,
    },
    "kiosk": {
        "enabled": True,
        "browser": "auto",
        "profile_dir": "",
        "disable_screensaver": True,
        "use_gpu": True,
        "browser_restart_seconds": 3,
        "browser_restart_max_seconds": 60,
        "browser_stable_seconds": 30,
        "browser_max_failures": 8,
        "log_file": "~/.cache/StorchCam/chromium.log",
        "log_max_bytes": 2097152,
        "log_backups": 3,
        "extra_flags": [],
    },
    "screen": {
        "hardware_profile": "generic",
        "output": "auto",
        "rotation": "none",
        "touch_device": "",
        "touch_matrix": "",
    },
    "location": {
        "label": "Berlin",
        "latitude": 52.5200,
        "longitude": 13.4050,
    },
    "stream": {
        "url": "",
        "muted": True,
        "autoplay": True,
        "quality": "auto",
        # Höchste Bildrate im eigenen Player: 30 schont den Pi, 60 ist flüssiger.
        "max_fps": 30,
        # Ton der Kamera: aus, leise und aus bei Musik („duck“) oder an.
        "audio": "off",
        # Gemerkte Kameras; die Reihenfolge bestimmt den Ersatz bei Ausfall.
        "favorites": [],
        "auto_fallback": True,
    },
    "admin": {
        "pin_hash": "",
        "require_pin": True,
    },
    "youtube": {
        "api_key": "",
        "default_query": "Storch",
    },
    "music": {
        "source": "off",
        "spotify": {"client_id": ""},
        "radio": {"favorites": []},
        # Klangregler für alles, was der Pi selbst abspielt (Radio, YouTube Music).
        "equalizer": {"preset": "flat", "gains": [0, 0, 0, 0, 0, 0]},
    },
    "ui": {
        "theme": "dark",
        "sky": "auto",
        "layout_profile": "auto",
        # Leseabstand zum Bildschirm in Metern (1, 2 oder 3 und mehr); skaliert die Infoleiste.
        "distance": 2,
        # Infoleiste nach so vielen Sekunden ohne Berührung verkleinern (0 = nie).
        "compact_after": 120,
        "setup_done": False,
        "night": {"enabled": False, "start": "23:00", "end": "06:00", "screen_off": False},
        "clock": {"enabled": True},
        "weather": {"enabled": True},
        "transit": {"enabled": False},
        "system": {"enabled": False},
    },
    "weather": {
        "provider": "openmeteo",
        "refresh_seconds": 300,
        "forecast_hours": 8,
        "rain_mm_threshold": 0.1,
        "rain_probability_threshold": 45,
        # Amtliche Warnungen des Deutschen Wetterdienstes (über Bright Sky) ab dieser Stufe zeigen.
        "warnings": "moderate",
    },
    "transit": {
        "provider": "vrr",
        "refresh_seconds": 60,
        "default_max_rows": 2,
        "target_len": 16,
        # Losgeh-Zeit (Abfahrt minus Gehzeit) statt Abfahrtszeit zeigen.
        "leave_mode": True,
        "stops": [],
    },
    "gaming": {
        # Kamera am Pi-Bildschirm anhalten, solange der Gaming-Container läuft (mehr Leistung fürs Spiel).
        "pause_camera": False,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 2097152,
        "backups": 3,
    },
}
