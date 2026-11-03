# Fertige Programme fürs Abspielen holen und prüfen; nur die Binärdateien kommen ins Image.
# Deno ist die JavaScript-Laufzeit, die yt-dlp für YouTube braucht (deutlich schneller als QuickJS auf dem Pi);
# librespot macht den Pi zum Spotify-Lautsprecher und stammt aus dem raspotify-Paket.
FROM python:3.12-slim AS players
ARG DENO_VERSION=2.9.6
ARG DENO_SHA256=9a46afc6c392c7cd2ff71a31558935545b46408d0e87f7a86908c712721c046e
ARG RASPOTIFY_URL=https://github.com/dtcooper/raspotify/releases/download/0.48.3/raspotify_0.48.3.librespot.v0.8.0-939dc5e_arm64.deb
ARG RASPOTIFY_SHA256=2097b3994824cf17163ecc47c6f6a5a695202987e2ab0b650f50d663291c63f2
RUN mkdir -p /out && if [ "$(dpkg --print-architecture)" = "arm64" ]; then \
        python -c "import urllib.request as u; u.urlretrieve('https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-aarch64-unknown-linux-gnu.zip', '/tmp/deno.zip'); u.urlretrieve('${RASPOTIFY_URL}', '/tmp/raspotify.deb')" \
        && echo "${DENO_SHA256}  /tmp/deno.zip" | sha256sum -c - \
        && echo "${RASPOTIFY_SHA256}  /tmp/raspotify.deb" | sha256sum -c - \
        && python -m zipfile -e /tmp/deno.zip /out \
        && dpkg-deb --fsys-tarfile /tmp/raspotify.deb | tar -x -C /tmp ./usr/bin/librespot \
        && mv /tmp/usr/bin/librespot /out/ && chmod 755 /out/deno /out/librespot; \
    else echo "Abspielen nur auf arm64 (Raspberry Pi) eingebaut"; fi

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Berlin \
    LANG=C.UTF-8 \
    LANGUAGE=de \
    HOME=/data \
    STORCHCAM_CONFIG=/data/config.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        libegl1 \
        libgles2 \
        libgl1-mesa-dri \
        fonts-dejavu-core \
        ca-certificates \
        mpv \
        bluez \
        libpulse0 \
        pulseaudio-utils \
        libasound2 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/chromium/policies/managed \
    && echo '{"TranslateEnabled": false}' > /etc/chromium/policies/managed/kiosk.json

COPY --from=players /out/ /usr/local/bin/

# Aus einem temporären Quellordner installieren, damit im Image keine Build-Reste zurückbleiben.
COPY pyproject.toml README.md LICENSE /tmp/src/
COPY storchcam /tmp/src/storchcam
RUN pip install /tmp/src tzdata \
    && rm -rf /tmp/src \
    && mkdir -p /data && chown 1000:1000 /data

WORKDIR /app
COPY --chmod=644 docker-entrypoint.py ./

USER 1000:1000
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=360s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
