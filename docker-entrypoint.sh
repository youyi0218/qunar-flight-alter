#!/bin/sh
set -eu

APP_DIR="${APP_DIR:-/app}"
SEED_DIR="/opt/qunar-flight-alter-seed"

mkdir -p "$APP_DIR"

for file in \
  flight_monitor.py \
  config.example.json \
  README.md \
  requirements.txt \
  docker-compose.yml \
  Dockerfile \
  .gitignore \
  .dockerignore \
  docker-entrypoint.sh \
  LICENSE
do
  if [ ! -e "$APP_DIR/$file" ] && [ -e "$SEED_DIR/$file" ]; then
    cp "$SEED_DIR/$file" "$APP_DIR/$file"
  fi
done

if [ ! -e "$APP_DIR/config.json" ] && [ -e "$APP_DIR/config.example.json" ]; then
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
fi

if [ ! -e "$APP_DIR/cookie.json" ]; then
  echo "warning: /app/cookie.json not found; please place your Cookie-Editor exported cookie.json in the project directory." >&2
fi

exec "$@"
