# ctrip-flight-alter

A Ctrip-based flight price monitor. You only need to fill routes in `config.json` with departure city, arrival city, and departure date. The program will resolve supported Ctrip cities, build the page URL internally, fetch prices, and send notifications when prices meet your threshold.

## Features

- Config-only workflow; define routes only in `config.json`
- Supports exported cookie.json login cookies for full itinerary crawling; if cookies are missing or expired, the program falls back automatically
- Auto-resolves Ctrip domestic city code and city ID from the live site
- Builds the Ctrip one-way list URL from `departure_city`, `arrival_city`, and `departure_date`
- Uses itinerary API data first, falls back to DOM parsing, and finally falls back to Ctrip lowest-price APIs (12808/lowestPrice and calendar pricing) when only route/date pricing is available
- Keeps price history and shows recent price movement
- Supports PushPlus HTML notifications
- Supports Resend email notifications
- Supports one-shot CLI mode and scheduled service mode
- Supports Docker and Docker Compose deployment

## Install

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Prepare a browser

Install Chromium or Chrome locally. If auto-detection fails, set `config.json -> browser.executable_path`.

3. Copy the template

```bash
cp config.example.json config.json
```

4. Edit `config.json`

### Optional login cookie

If you export your logged-in Ctrip cookies to `cookie.json` in the project root, the crawler will load them automatically before opening the flight page. This is recommended because the logged-in itinerary API returns full flight details more reliably.

## Config

Important fields:

- `pushplus.token`
- `email.api_key`
- `email.from`
- `email.to`
- `service.schedule_times`
- `service.capture_lead_minutes`
- `routes`

Each item in `routes` should contain:

- `departure_city`
- `arrival_city`
- `departure_date`
- `expected_price`
- `enabled`

Example:

```jsonc
{
  "departure_city": "YOUR_DEPARTURE_CITY",
  "arrival_city": "YOUR_ARRIVAL_CITY",
  "departure_date": "2026-12-31",
  "expected_price": 500,
  "enabled": true
}
```

The program builds a Ctrip page URL like this internally:

```text
https://flights.ctrip.com/online/list/oneway-bjs-sha?depdate=2026-04-15&cabin=y_s_c_f&adult=1&child=0&infant=0
```

## Usage

Run once and send notifications:

```bash
python flight_monitor.py
```

Dry run and print JSON only:

```bash
python flight_monitor.py --dry-run --dump-json
```

Run as a scheduled service:

```bash
python flight_monitor.py --service
```

## Docker

### Build locally

```bash
docker build -t ctrip-flight-alter .
docker run --rm --user root -v $(pwd):/app ctrip-flight-alter
```

### Use the GitHub Actions image

```text
ghcr.io/youyi0218/ctrip-flight-alter:latest
```

### Docker Compose

```bash
docker compose up -d
```

## Main files

- `flight_monitor.py`
- `requirements.txt`
- `config.example.json`
- `docker-compose.yml`
- `Dockerfile`
- `.gitignore`
- `.dockerignore`
- `docker-entrypoint.sh`
- `LICENSE`

## Update image

```bash
docker login ghcr.io -u youyi0218
docker compose pull
docker compose up -d
```

If you only changed local config:

```bash
docker compose restart
```

Check logs:

```bash
docker compose logs -f
```

Stop service:

```bash
docker compose down
```

## Debug

Recommended first check:

```bash
python flight_monitor.py --dry-run --dump-json
```
