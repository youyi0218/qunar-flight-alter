# syntax=docker/dockerfile:1
#
# Lightweight image for running flight_monitor.py in service mode.
# - Installs system Chromium (used via config.json/browser executable auto-detection)
# - Skips Playwright browser downloads to reduce build time/size/network
# - Runs as non-root (Chromium sandbox friendly)

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Install Chromium + minimal runtime utils.
# chromium package pulls most required shared libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        dumb-init \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (Chromium cannot run as root without --no-sandbox).
RUN useradd -m -u 10001 -s /usr/sbin/nologin app \
    && mkdir -p /app /opt/ctrip-flight-alter-seed \
    && chown -R app:app /app /opt/ctrip-flight-alter-seed

WORKDIR /opt/ctrip-flight-alter-seed
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

COPY flight_monitor.py README.md config.example.json docker-compose.yml Dockerfile .gitignore .dockerignore docker-entrypoint.sh LICENSE ./
RUN chmod +x /opt/ctrip-flight-alter-seed/docker-entrypoint.sh

USER app

# Runtime working directory (mount the whole project here if desired).
WORKDIR /app

ENTRYPOINT ["dumb-init", "--", "/opt/ctrip-flight-alter-seed/docker-entrypoint.sh"]
CMD ["python", "/app/flight_monitor.py", "--service", "--config", "/app/config.json"]

