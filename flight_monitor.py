#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_ONEBOT_CONFIG_PATH = Path("onebot-config.json")
DEFAULT_COOKIE_PATH = Path("cookie.json")
DEFAULT_BROWSER_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/snap/bin/chromium"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
]
PUSHPLUS_URL = "https://www.pushplus.plus/send"
CTRIP_HOME_URL = "https://flights.ctrip.com/online/channel/domestic"
CTRIP_BATCH_SEARCH_KEYWORD = "/search/api/search/batchSearch"
CTRIP_REPO_LOWEST_PRICE_URL = "https://flights.ctrip.com/itinerary/api/12808/lowestPrice"
CTRIP_LOWEST_PRICE_URL = "https://m.ctrip.com/restapi/soa2/15380/bjjson/FlightIntlAndInlandLowestPriceSearch"
CTRIP_CITY_SELECTOR_REMARK_RE = re.compile(
    r"选择城市\[(?P<display>[^|]+)\|(?P<label>[^|]+)\((?P<code>[A-Z]{3})\)\|(?P<city_id>\d+)\|(?P<url_code>[A-Z]{3})\]"
)
CTRIP_MS_DATE_RE = re.compile(r"/Date\((?P<ms>-?\d+)(?:[+-]\d+)?\)/")

INIT_SCRIPT = r"""
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (_) {}
  try {
    window.chrome = window.chrome || { runtime: {} };
  } catch (_) {}
  try {
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  } catch (_) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
  } catch (_) {}

  const state = {
    requests: [],
  };
  window.__flightMonitor = state;

  const captureResponse = (meta, status, responseText) => {
    state.requests.push({
      url: meta.url || '',
      method: meta.method || '',
      body: meta.body || '',
      headers: meta.headers || {},
      status: status || 0,
      responseText: responseText || '',
      ts: Date.now(),
    });
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function(method, url) {
    this.__flightMonitorMeta = { method, url, headers: {} };
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    if (this.__flightMonitorMeta) {
      this.__flightMonitorMeta.headers[name] = value;
    }
    return originalSetRequestHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    const meta = this.__flightMonitorMeta || { headers: {} };
    meta.body = typeof body === 'string' ? body : '';
    this.addEventListener('loadend', function() {
      let responseText = '';
      try {
        responseText = this.responseText || '';
      } catch (_) {}
      captureResponse(meta, this.status, responseText);
    });
    return originalSend.apply(this, arguments);
  };

  const originalFetch = window.fetch;
  window.fetch = async function(input, init) {
    const meta = {
      method: (init && init.method) || (input && input.method) || 'GET',
      url: typeof input === 'string' ? input : ((input && input.url) || ''),
      headers: (init && init.headers) || {},
      body: (init && typeof init.body === 'string') ? init.body : '',
    };
    const response = await originalFetch.apply(this, arguments);
    try {
      const cloned = response.clone();
      const responseText = await cloned.text();
      captureResponse(meta, response.status, responseText);
    } catch (_) {}
    return response;
  };

})();
"""


@dataclass
class FlightSegment:
    airline: str
    short_airline: str
    flight_no: str
    departure_date: str
    departure_time: str
    departure_airport: str
    arrival_date: str
    arrival_time: str
    arrival_airport: str
    flight_duration: str
    arrival_day_note: str
    aircraft: str


@dataclass
class Ticket:
    route: str
    departure_city: str
    arrival_city: str
    departure_date: str
    arrival_date: str
    arrival_day_offset: int
    flight_type: str
    airlines: str
    flight_numbers: str
    departure_time: str
    arrival_time: str
    departure_airport: str
    arrival_airport: str
    total_duration: str
    flight_duration: str
    transfer_city: str
    transfer_duration: str
    price: int
    discount: str
    labels: list[str]
    segments: list[FlightSegment]


@dataclass(frozen=True)
class ScheduleOccurrence:
    slot: str
    slot_time: datetime
    slot_key: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="携程机票价格监控与 PushPlus 推送")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只抓取和打印，不发送 PushPlus")
    parser.add_argument("--dump-json", action="store_true", help="输出结构化 JSON 结果")
    parser.add_argument("--service", action="store_true", help="以定时服务模式运行")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def strip_json_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    string_quote = ""
    escaped = False
    i = 0
    length = len(text)

    while i < length:
        char = text[i]
        nxt = text[i + 1] if i + 1 < length else ""

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == string_quote:
                in_string = False
            i += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            string_quote = char
            result.append(char)
            i += 1
            continue

        if char == "/" and nxt == "/":
            i += 2
            while i < length and text[i] not in "\r\n":
                i += 1
            continue

        if char == "/" and nxt == "*":
            i += 2
            while i + 1 < length and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue

        result.append(char)
        i += 1

    return "".join(result)


def load_jsonc(path: Path) -> Any:
    return json.loads(strip_json_comments(path.read_text(encoding="utf-8")))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_output(text: str) -> None:
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
    sys.stdout.flush()


def normalize_cookie_same_site(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    mapping = {
        "lax": "Lax",
        "strict": "Strict",
        "none": "None",
        "no_restriction": "None",
    }
    return mapping.get(raw)


def load_cookie_file(path: Path = DEFAULT_COOKIE_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = load_json(path)
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        raw = raw["cookies"]
    if not isinstance(raw, list):
        raise ValueError(f"{path} 必须是浏览器导出的 Cookie JSON 数组")

    cookies: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        if not name or not domain:
            continue
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(item.get("path") or "/"),
            "httpOnly": bool(item.get("httpOnly", False)),
            "secure": bool(item.get("secure", False)),
        }
        same_site = normalize_cookie_same_site(item.get("sameSite"))
        if same_site:
            cookie["sameSite"] = same_site
        expires = item.get("expirationDate")
        if expires not in (None, "", 0, 0.0):
            try:
                cookie["expires"] = float(expires)
            except (TypeError, ValueError):
                pass
        cookies.append(cookie)
    return cookies


def build_ctrip_requests_session(cookies: list[dict[str, Any]]) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }
    )
    for cookie in cookies:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return session


def ensure_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"未找到配置文件: {path}")
    config = load_jsonc(path)
    if not isinstance(config, dict):
        raise ValueError("config.json 必须是 JSON 对象")

    config.setdefault("state_file", ".flight_monitor_state.json")
    config.setdefault("history_file", ".flight_monitor_history.json")
    config.setdefault("notify_empty_results", True)
    config.setdefault("routes", [])
    config.setdefault(
        "browser",
        {
            "headless": True,
            "wait_timeout_ms": 25000,
            "poll_interval_ms": 500,
            "request_retries": 3,
            "viewport_width": 1280,
            "viewport_height": 720,
            "executable_path": "",
            "block_images": True,
            "block_fonts": True,
            "block_media": True,
            "block_stylesheets": True,
            "block_tracking": True,
        },
    )
    config.setdefault(
        "pushplus",
        {
            "token": "",
            "channel": "wechat",
            "template": "markdown",
        },
    )
    config.setdefault(
        "email",
        {
            "enabled": False,
            "provider": "resend",
            "api_key": "",
            "from": "",
            "to": [],
        },
    )
    config.setdefault(
        "service",
        {
            "timezone": "Asia/Shanghai",
            "capture_lead_minutes": 10,
            "schedule_times": ["09:00"],
            "sleep_cap_seconds": 60,
            "schedule_grace_seconds": 300,
            "history_retention_days": 7,
        },
    )
    for route in config["routes"]:
        route.setdefault("expected_price", None)
        route.setdefault("enabled", True)
    return config


def load_onebot_config(path: Path = DEFAULT_ONEBOT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "enabled": False,
            "base_url": "",
            "access_token": "",
            "targets": [],
        }
    config = load_jsonc(path)
    if not isinstance(config, dict):
        raise ValueError("onebot-config.json 必须是 JSON 对象")
    config.setdefault("enabled", False)
    config.setdefault("base_url", "")
    config.setdefault("access_token", "")
    config.setdefault("targets", [])
    return config




def detect_browser_executable(config: dict[str, Any]) -> str:
    browser_cfg = config.get("browser", {})
    configured = str(browser_cfg.get("executable_path") or "").strip()
    if configured:
        if Path(configured).exists():
            return configured
        raise FileNotFoundError(f"浏览器路径不存在: {configured}")

    for candidate in DEFAULT_BROWSER_PATHS:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("未找到可用的浏览器，请在 config.json.browser.executable_path 中指定 Chromium/Chrome 路径")


def normalize_city_name(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def parse_city_selector_remark(remark: str) -> dict[str, Any]:
    match = CTRIP_CITY_SELECTOR_REMARK_RE.search(str(remark or "").strip())
    if not match:
        raise ValueError(f"无法解析携程城市标记: {remark}")
    return {
        "name": match.group("display").strip(),
        "code": match.group("code").strip().upper(),
        "city_id": int(match.group("city_id")),
    }


def iter_city_aliases(name: str) -> list[str]:
    aliases = [normalize_city_name(name)]
    if name.startswith("中国") and len(name) > 2:
        aliases.append(normalize_city_name(name[2:]))
    trimmed = re.sub(r"[（(].*?[)）]", "", name).strip()
    if trimmed and normalize_city_name(trimmed) not in aliases:
        aliases.append(normalize_city_name(trimmed))
    return [alias for alias in aliases if alias]


def build_ctrip_url(route: dict[str, Any]) -> str:
    departure_code = str(route["departure_city_code"]).strip().lower()
    arrival_code = str(route["arrival_city_code"]).strip().lower()
    departure_date = str(route["departure_date"]).strip()
    return (
        f"https://flights.ctrip.com/online/list/oneway-{departure_code}-{arrival_code}"
        f"?depdate={departure_date}&cabin=y_s_c_f&adult=1&child=0&infant=0"
    )


def build_ctrip_repo_lowest_price_params(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "flightWay": "Oneway",
        "dcity": str(route["departure_city_code"]).strip().upper(),
        "acity": str(route["arrival_city_code"]).strip().upper(),
        "direct": "false",
        "army": "false",
    }


def build_ctrip_lowest_price_payload(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "departNewCityCode": str(route["departure_city_code"]).strip().upper(),
        "arriveNewCityCode": str(route["arrival_city_code"]).strip().upper(),
        "startDate": str(route["departure_date"]).strip(),
        "grade": 15,
        "flag": 0,
        "channelName": "FlightOnline",
        "searchType": 1,
        "passengerList": [{"passengercount": 1, "passengertype": "Adult"}],
        "calendarSelections": [{"selectionType": 8, "selectionContent": ["15"]}],
    }


def parse_ctrip_ms_date(value: Any) -> str:
    match = CTRIP_MS_DATE_RE.search(str(value or "").strip())
    if not match:
        return ""
    try:
        dt = datetime.fromtimestamp(int(match.group("ms")) / 1000, ZoneInfo("Asia/Shanghai"))
    except (ValueError, OSError):
        return ""
    return dt.strftime("%Y-%m-%d")


def departure_day_key(route: dict[str, Any]) -> str:
    return str(route.get("departure_date") or "").replace("-", "").strip()


def combine_airport(name: str | None, terminal: str | None) -> str:
    name = (name or "").strip()
    terminal = (terminal or "").strip()
    return f"{name}{terminal}" if terminal else name


def normalize_duration_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "小时" in raw or "分钟" in raw:
        return raw
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", raw.lower())
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        return humanize_minutes(hours * 60 + minutes)
    return raw


def parse_duration_to_minutes(value: Any) -> int | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if "小时" in raw or "分钟" in raw:
        hours = re.search(r"(\d+)\s*小时", raw)
        minutes = re.search(r"(\d+)\s*分钟", raw)
        return int(hours.group(1) if hours else 0) * 60 + int(minutes.group(1) if minutes else 0)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", raw)
    if match:
        return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    return None


def humanize_minutes(minutes: int | None) -> str:
    if minutes is None:
        return ""
    if minutes < 0:
        minutes = 0
    hours, remain = divmod(minutes, 60)
    if hours and remain:
        return f"{hours}小时{remain}分钟"
    if hours:
        return f"{hours}小时"
    return f"{remain}分钟"


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_datetime_ymd_hm(day: str | None, hm: str | None) -> datetime | None:
    if not day or not hm:
        return None
    try:
        return datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def get_arrival_offset(departure_day: str | None, arrival_day: str | None) -> int:
    dep = parse_iso_date(departure_day)
    arr = parse_iso_date(arrival_day)
    if not dep or not arr:
        return 0
    return (arr - dep).days


def format_day_offset(offset: int) -> str:
    return f"+{offset}天" if offset > 0 else ""


def dedupe_in_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def extract_price_labels(raw_labels: Any) -> list[str]:
    result: list[str] = []
    if isinstance(raw_labels, list):
        for item in raw_labels:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or "").strip()
                if label:
                    result.append(label)
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
    return dedupe_in_order(result)


def extract_segment_dicts(flight: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if isinstance(flight.get("binfo"), dict):
        segments.append(flight["binfo"])
    index = 1
    while True:
        key = f"binfo{index}"
        value = flight.get(key)
        if not isinstance(value, dict):
            break
        segments.append(value)
        index += 1
    return segments


def parse_segment(segment: dict[str, Any]) -> FlightSegment:
    dep_date = str(segment.get("depDate") or segment.get("date") or "")
    arr_date = str(segment.get("arrDate") or dep_date or "")
    arrival_day_note = str(segment.get("crossDayDesc") or format_day_offset(get_arrival_offset(dep_date, arr_date)))
    return FlightSegment(
        airline=str(segment.get("fullName") or segment.get("name") or segment.get("shortName") or segment.get("shortCarrier") or ""),
        short_airline=str(segment.get("shortName") or segment.get("shortCarrier") or ""),
        flight_no=str(segment.get("airCode") or ""),
        departure_date=dep_date,
        departure_time=str(segment.get("depTime") or ""),
        departure_airport=combine_airport(segment.get("depAirport"), segment.get("depTerminal")),
        arrival_date=arr_date,
        arrival_time=str(segment.get("arrTime") or ""),
        arrival_airport=combine_airport(segment.get("arrAirport"), segment.get("arrTerminal")),
        flight_duration=normalize_duration_text(segment.get("flightTime")),
        arrival_day_note=arrival_day_note,
        aircraft=str(segment.get("planeFullType") or segment.get("planeType") or ""),
    )


def compute_total_duration(segments: list[FlightSegment]) -> str:
    if not segments:
        return ""
    dep_dt = parse_datetime_ymd_hm(segments[0].departure_date, segments[0].departure_time)
    arr_dt = parse_datetime_ymd_hm(segments[-1].arrival_date, segments[-1].arrival_time)
    if not dep_dt or not arr_dt:
        return ""
    return humanize_minutes(int((arr_dt - dep_dt).total_seconds() // 60))


def compute_air_duration(segments: list[FlightSegment]) -> str:
    total = 0
    has_value = False
    for segment in segments:
        minutes = parse_duration_to_minutes(segment.flight_duration)
        if minutes is None:
            continue
        has_value = True
        total += minutes
    if has_value:
        return humanize_minutes(total)
    if len(segments) == 1:
        return segments[0].flight_duration
    return " / ".join([segment.flight_duration for segment in segments if segment.flight_duration])


def classify_flight(flight: dict[str, Any], segments_raw: list[dict[str, Any]]) -> str:
    if str(flight.get("flightType") or "") == "listMore":
        return "中转"
    if segments_raw and bool(segments_raw[0].get("stops")):
        return "经停"
    return "直飞"


def parse_ticket(route: dict[str, Any], flight: dict[str, Any]) -> Ticket:
    segments_raw = extract_segment_dicts(flight)
    segments = [parse_segment(segment) for segment in segments_raw]
    if not segments:
        raise ValueError("未找到可解析的航段信息")

    first = segments[0]
    last = segments[-1]
    arrival_day_offset = get_arrival_offset(first.departure_date, last.arrival_date)
    labels = extract_price_labels(flight.get("priceLabel"))
    discount = str(flight.get("discountStr") or "").strip()
    if discount and discount not in labels:
        labels = [discount] + labels
    airlines = " / ".join(
        dedupe_in_order([segment.airline or segment.short_airline for segment in segments])
    )
    flight_numbers = " / ".join([segment.flight_no for segment in segments if segment.flight_no])
    transfer_duration = normalize_duration_text(flight.get("transTime"))
    return Ticket(
        route=f"{route['departure_city']} → {route['arrival_city']}",
        departure_city=route["departure_city"],
        arrival_city=route["arrival_city"],
        departure_date=first.departure_date,
        arrival_date=last.arrival_date,
        arrival_day_offset=arrival_day_offset,
        flight_type=classify_flight(flight, segments_raw),
        airlines=airlines,
        flight_numbers=flight_numbers,
        departure_time=first.departure_time,
        arrival_time=last.arrival_time,
        departure_airport=first.departure_airport,
        arrival_airport=last.arrival_airport,
        total_duration=compute_total_duration(segments),
        flight_duration=compute_air_duration(segments),
        transfer_city=str(flight.get("transCity") or "").strip(),
        transfer_duration=transfer_duration,
        price=int(flight.get("minPrice") or 0),
        discount=discount,
        labels=labels,
        segments=segments,
    )


def parse_flights(route: dict[str, Any], payload: dict[str, Any]) -> list[Ticket]:
    flights = payload.get("data", {}).get("flights", [])
    return [parse_ticket(route, flight) for flight in flights if isinstance(flight, dict)]


def parse_day_offset_note(value: Any) -> int:
    match = re.search(r"\+(\d+)\s*天", str(value or ""))
    return int(match.group(1)) if match else 0


def compute_total_duration_from_schedule(departure_date: str, departure_time: str, arrival_time: str, arrival_day_offset: int) -> str:
    dep_dt = parse_datetime_ymd_hm(departure_date, departure_time)
    dep_day = parse_iso_date(departure_date)
    if not dep_dt or not dep_day:
        return ""
    arr_day = dep_day + timedelta(days=max(0, arrival_day_offset))
    arr_dt = parse_datetime_ymd_hm(arr_day.strftime("%Y-%m-%d"), arrival_time)
    if not arr_dt:
        return ""
    return humanize_minutes(int((arr_dt - dep_dt).total_seconds() // 60))


def parse_display_ticket(route: dict[str, Any], row: dict[str, Any]) -> Ticket:
    arrival_day_offset = parse_day_offset_note(row.get("arrival_day_note"))
    arrival_date = (
        (parse_iso_date(route["departure_date"]) or date.today()) + timedelta(days=arrival_day_offset)
    ).strftime("%Y-%m-%d")
    range_text = normalize_duration_text(row.get("range_text"))
    total_duration = compute_total_duration_from_schedule(
        route["departure_date"],
        str(row.get("departure_time") or ""),
        str(row.get("arrival_time") or ""),
        arrival_day_offset,
    )
    if not total_duration:
        total_duration = range_text if "停留" not in range_text else ""
    flight_duration = range_text if range_text and "停留" not in range_text else total_duration
    discount = str(row.get("discount") or "").strip()
    labels = dedupe_in_order([str(item).strip() for item in (row.get("labels") or []) if str(item).strip()])
    if discount and discount not in labels:
        labels = [discount] + labels
    flight_type = str(row.get("flight_type") or "").strip() or "直飞"
    return Ticket(
        route=f"{route['departure_city']} → {route['arrival_city']}",
        departure_city=route["departure_city"],
        arrival_city=route["arrival_city"],
        departure_date=route["departure_date"],
        arrival_date=arrival_date,
        arrival_day_offset=arrival_day_offset,
        flight_type=flight_type,
        airlines=str(row.get("airlines") or "").strip(),
        flight_numbers=str(row.get("flight_numbers") or "").strip(),
        departure_time=str(row.get("departure_time") or "").strip(),
        arrival_time=str(row.get("arrival_time") or "").strip(),
        departure_airport=str(row.get("departure_airport") or "").strip(),
        arrival_airport=str(row.get("arrival_airport") or "").strip(),
        total_duration=total_duration,
        flight_duration=flight_duration,
        transfer_city=str(row.get("transfer_city") or "").strip(),
        transfer_duration=normalize_duration_text(row.get("transfer_duration")),
        price=int(row.get("price") or 0),
        discount=discount,
        labels=labels,
        segments=[],
    )


def parse_display_tickets(route: dict[str, Any], rows: list[dict[str, Any]]) -> list[Ticket]:
    tickets: list[Ticket] = []
    for row in rows:
        if not str(row.get("price") or "").isdigit():
            continue
        try:
            tickets.append(parse_display_ticket(route, row))
        except Exception:
            continue
    tickets.sort(key=lambda item: (item.price, item.departure_time, item.arrival_time, item.flight_numbers))
    return tickets


def split_datetime_text(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M")
    except ValueError:
        pass
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%H:%M")
    except ValueError:
        return "", ""


def format_discount_rate(value: Any) -> str:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return ""
    if rate <= 0:
        return ""
    fold = rate * 10 if rate <= 1 else rate
    fold = round(fold, 1)
    if float(fold).is_integer():
        return f"{int(fold)}折"
    return f"{fold:.1f}折"


def compute_transfer_duration_from_flights(flights: list[dict[str, Any]]) -> str:
    durations: list[str] = []
    for current, nxt in zip(flights, flights[1:]):
        current_arrival = str(current.get("arrivalDateTime") or "").strip()
        next_departure = str(nxt.get("departureDateTime") or "").strip()
        if not current_arrival or not next_departure:
            continue
        try:
            arr_dt = datetime.strptime(current_arrival, "%Y-%m-%d %H:%M:%S")
            dep_dt = datetime.strptime(next_departure, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        gap_minutes = int((dep_dt - arr_dt).total_seconds() // 60)
        if gap_minutes > 0:
            durations.append(humanize_minutes(gap_minutes))
    return " / ".join(durations)


def extract_ctrip_price_labels(price: dict[str, Any]) -> tuple[str, list[str]]:
    labels: list[str] = []
    baggage_tag = str((price.get("baggage") or {}).get("baggageTag") or "").strip()
    if baggage_tag:
        labels.append(baggage_tag)

    for tag in price.get("priceTags") or []:
        if not isinstance(tag, dict):
            continue
        label = str(tag.get("label") or tag.get("title") or "").strip()
        if label:
            labels.append(label)

    for unit in price.get("priceUnitList") or []:
        if not isinstance(unit, dict):
            continue
        for seat in unit.get("flightSeatList") or []:
            if not isinstance(seat, dict):
                continue
            special_name = str(seat.get("specialClassName") or "").strip()
            if special_name:
                labels.append(special_name)

    discount = format_discount_rate(
        next(
            (
                seat.get("discountRate")
                for unit in price.get("priceUnitList") or []
                if isinstance(unit, dict)
                for seat in unit.get("flightSeatList") or []
                if isinstance(seat, dict) and seat.get("discountRate") not in (None, "")
            ),
            None,
        )
    )
    if discount:
        labels.insert(0, discount)
    return discount, dedupe_in_order(labels)


def parse_ctrip_segment(flight: dict[str, Any]) -> FlightSegment:
    departure_date, departure_time = split_datetime_text(flight.get("departureDateTime"))
    arrival_date, arrival_time = split_datetime_text(flight.get("arrivalDateTime"))
    arrival_day_offset = get_arrival_offset(departure_date, arrival_date)
    airline_name = str(flight.get("marketAirlineName") or flight.get("operateAirlineName") or "").strip()
    return FlightSegment(
        airline=airline_name,
        short_airline=airline_name,
        flight_no=str(flight.get("flightNo") or "").strip(),
        departure_date=departure_date,
        departure_time=departure_time,
        departure_airport=combine_airport(flight.get("departureAirportName"), flight.get("departureTerminal")),
        arrival_date=arrival_date,
        arrival_time=arrival_time,
        arrival_airport=combine_airport(flight.get("arrivalAirportName"), flight.get("arrivalTerminal")),
        flight_duration=humanize_minutes(int(flight.get("duration") or 0)) if flight.get("duration") else "",
        arrival_day_note=format_day_offset(arrival_day_offset),
        aircraft=str(flight.get("aircraftName") or "").strip(),
    )


def parse_ctrip_ticket(route: dict[str, Any], itinerary: dict[str, Any]) -> Ticket:
    flight_segments = itinerary.get("flightSegments") or []
    flight_group = flight_segments[0] if flight_segments else {}
    flights = [item for item in flight_group.get("flightList") or [] if isinstance(item, dict)]
    if not flights:
        raise ValueError("未找到可解析的携程航班信息")

    segments = [parse_ctrip_segment(item) for item in flights]
    first = segments[0]
    last = segments[-1]
    arrival_day_offset = get_arrival_offset(first.departure_date, last.arrival_date)
    best_price = min(
        [item for item in itinerary.get("priceList") or [] if isinstance(item, dict) and item.get("adultPrice") not in (None, "")]
        or [{}],
        key=lambda item: float(item.get("adultPrice") or 10**9),
    )
    discount, labels = extract_ctrip_price_labels(best_price)
    airlines = " / ".join(
        dedupe_in_order(
            [segment.airline for segment in segments if segment.airline]
            or [str(flight_group.get("airlineName") or "").strip()]
        )
    )
    flight_numbers = " / ".join([segment.flight_no for segment in segments if segment.flight_no])
    transfer_count = int(flight_group.get("transferCount") or 0)
    stop_count = int(flight_group.get("stopCount") or 0)
    if transfer_count > 0 or len(segments) > 1:
        flight_type = "中转"
    elif stop_count > 0:
        flight_type = "经停"
    else:
        flight_type = "直飞"
    transfer_city = " / ".join(
        dedupe_in_order(
            [
                str(flight.get("arrivalCityName") or "").strip()
                for flight in flights[:-1]
                if str(flight.get("arrivalCityName") or "").strip()
            ]
        )
    )
    transfer_duration = compute_transfer_duration_from_flights(flights)
    total_duration = humanize_minutes(int(flight_group.get("duration") or 0)) if flight_group.get("duration") else compute_total_duration(segments)
    return Ticket(
        route=f"{route['departure_city']} → {route['arrival_city']}",
        departure_city=route["departure_city"],
        arrival_city=route["arrival_city"],
        departure_date=first.departure_date,
        arrival_date=last.arrival_date,
        arrival_day_offset=arrival_day_offset,
        flight_type=flight_type,
        airlines=airlines,
        flight_numbers=flight_numbers,
        departure_time=first.departure_time,
        arrival_time=last.arrival_time,
        departure_airport=first.departure_airport,
        arrival_airport=last.arrival_airport,
        total_duration=total_duration,
        flight_duration=compute_air_duration(segments),
        transfer_city=transfer_city,
        transfer_duration=transfer_duration,
        price=int(float(best_price.get("adultPrice") or 0)),
        discount=discount,
        labels=labels,
        segments=segments,
    )


def parse_ctrip_flights(route: dict[str, Any], payload: dict[str, Any]) -> list[Ticket]:
    itineraries = payload.get("data", {}).get("flightItineraryList", [])
    tickets = [parse_ctrip_ticket(route, item) for item in itineraries if isinstance(item, dict)]
    tickets.sort(key=lambda item: (item.price, item.departure_time, item.arrival_time, item.flight_numbers))
    return tickets


def build_ctrip_lowest_price_ticket(
    route: dict[str, Any],
    price: int,
    total_price: int | None = None,
    extra_labels: list[str] | None = None,
) -> Ticket:
    labels = ["日历最低价", "仅含日期价格，不含具体航班"]
    if extra_labels:
        labels.extend(extra_labels)
    if total_price and total_price != price:
        labels.append(f"含税约￥{total_price}")
    return Ticket(
        route=f"{route['departure_city']} → {route['arrival_city']}",
        departure_city=route["departure_city"],
        arrival_city=route["arrival_city"],
        departure_date=str(route.get("departure_date") or "").strip(),
        arrival_date=str(route.get("departure_date") or "").strip(),
        arrival_day_offset=0,
        flight_type="日历最低价",
        airlines="携程日历价",
        flight_numbers="LOWEST-PRICE",
        departure_time="--:--",
        arrival_time="--:--",
        departure_airport=route["departure_city"],
        arrival_airport=route["arrival_city"],
        total_duration="",
        flight_duration="",
        transfer_city="",
        transfer_duration="",
        price=price,
        discount="",
        labels=dedupe_in_order([label for label in labels if label]),
        segments=[],
    )


def extract_ctrip_repo_lowest_price(route: dict[str, Any], payload: dict[str, Any]) -> int | None:
    price_rows = ((payload.get("data") or {}).get("oneWayPrice") or [])
    if not price_rows or not isinstance(price_rows[0], dict):
        return None
    day_key = departure_day_key(route)
    raw_price = price_rows[0].get(day_key)
    if raw_price in (None, ""):
        return None
    try:
        return int(round(float(raw_price)))
    except (TypeError, ValueError):
        return None


def parse_ctrip_lowest_price_tickets(
    route: dict[str, Any],
    payload: dict[str, Any],
    price_override: int | None = None,
) -> list[Ticket]:
    target_date = str(route.get("departure_date") or "").strip()
    tickets: list[Ticket] = []
    for item in payload.get("priceList") or []:
        if not isinstance(item, dict):
            continue
        departure_date = parse_ctrip_ms_date(item.get("departDate"))
        if departure_date != target_date:
            continue
        base_price_raw = item.get("transportPrice")
        if base_price_raw in (None, ""):
            base_price_raw = item.get("price")
        try:
            base_price = int(round(float(base_price_raw)))
        except (TypeError, ValueError):
            continue

        price = price_override if price_override is not None else base_price
        total_price: int | None = None
        try:
            if item.get("totalPrice") not in (None, ""):
                total_price = int(round(float(item.get("totalPrice"))))
        except (TypeError, ValueError):
            total_price = None

        labels: list[str] = []
        direct_text = str(item.get("directCalendarText") or "").strip()
        if direct_text:
            labels.append(direct_text)
        tickets.append(build_ctrip_lowest_price_ticket(route, price=price, total_price=total_price, extra_labels=labels))
    tickets.sort(key=lambda item: (item.price, item.departure_time, item.arrival_time, item.flight_numbers))
    return tickets


def fetch_ctrip_repo_lowest_price_payload(route: dict[str, Any], session: requests.Session | None = None) -> dict[str, Any]:
    client = session or requests.Session()
    response = client.get(
        CTRIP_REPO_LOWEST_PRICE_URL,
        params=build_ctrip_repo_lowest_price_params(route),
        headers={
            "Referer": str(route.get("source_url") or CTRIP_HOME_URL),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_ctrip_lowest_price_payload(
    route: dict[str, Any],
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = session or requests.Session()
    response = client.post(
        CTRIP_LOWEST_PRICE_URL,
        json=build_ctrip_lowest_price_payload(route),
        headers={
            "Referer": str(route.get("source_url") or CTRIP_HOME_URL),
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def resolve_ctrip_tickets(
    route: dict[str, Any],
    payload: dict[str, Any],
    display_rows: list[dict[str, Any]],
) -> tuple[list[Ticket], str]:
    status = int(payload.get("status", -1))

    if status == 0:
        tickets = parse_ctrip_flights(route, payload)
        if tickets:
            if display_rows:
                apply_display_prices(tickets, display_rows)
            return tickets, "api"

        if display_rows:
            safe_output(
                "警告：携程接口返回 status=0，但 flightItineraryList 为空，"
                f"已自动切换为页面 DOM 兜底解析：{route['departure_city']} -> "
                f"{route['arrival_city']} {route['departure_date']}"
            )
            return parse_display_tickets(route, display_rows), "dom"

        safe_output(
            "警告：携程接口返回 status=0，但 flightItineraryList 为空，且页面未解析到机票卡片，"
            f"按空结果处理：{route['departure_city']} -> {route['arrival_city']} {route['departure_date']}"
        )
        return [], "empty"

    if display_rows:
        safe_output(
            f"警告：携程接口返回 status={status}，已自动切换为页面 DOM 兜底解析："
            f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']}"
        )
        return parse_display_tickets(route, display_rows), "dom"

    safe_output(
        f"警告：携程接口返回 status={status}，且页面未解析到机票卡片，按空结果处理："
        f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']}"
    )
    return [], "empty"


def format_segment_line(segment: FlightSegment) -> str:
    aircraft = f"｜{segment.aircraft}" if segment.aircraft else ""
    arrive_suffix = f" {segment.arrival_day_note}" if segment.arrival_day_note else ""
    return (
        f"- {segment.airline} {segment.flight_no}{aircraft}\n"
        f"  - 起飞：{segment.departure_date} {segment.departure_time} {segment.departure_airport}\n"
        f"  - 到达：{segment.arrival_date} {segment.arrival_time}{arrive_suffix} {segment.arrival_airport}\n"
        f"  - 飞行时长：{segment.flight_duration or '未知'}"
    )


def calc_change_label(current_price: int, previous_price: int | None) -> str:
    if previous_price in (None, 0):
        return "-"
    diff = current_price - previous_price
    pct = diff / previous_price * 100
    sign = "+" if diff > 0 else ""
    return f"{sign}{pct:.1f}%"


def normalize_price_table(curve: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not curve:
        return []
    rows: list[dict[str, Any]] = []
    previous_price: int | None = None
    for item in curve:
        price = int(item["price"])
        rows.append(
            {
                "time": str(item["time"]),
                "price": price,
                "change": calc_change_label(price, previous_price),
            }
        )
        previous_price = price
    return rows


def html_text(value: Any) -> str:
    return html.escape(str(value or ""))


def minify_html(content: str) -> str:
    compact = re.sub(r">\s+<", "><", content.strip())
    compact = re.sub(r"\n\s+", "\n", compact)
    return compact


def build_ticket_card_html(index: int, ticket: Ticket, curve: list[dict[str, Any]] | None = None) -> str:
    table_rows = normalize_price_table(curve)
    table_html = "".join(
        [
            "<tr>"
            f"<td>{html_text(row['time'])}</td>"
            f"<td>￥{row['price']}</td>"
            f"<td>{html_text(row['change'])}</td>"
            "</tr>"
            for row in table_rows
        ]
    )
    if not table_html:
        table_html = "<tr><td colspan='3'>暂无历史波动</td></tr>"

    arrive_suffix = f" {ticket.arrival_date}" if ticket.arrival_day_offset > 0 else ""
    return f"""
    <article class="ticket-card">
      <div class="ticket-order">#{index}</div>
      <div class="ticket-main">
        <div class="ticket-time">{html_text(ticket.departure_time)} → {html_text(ticket.arrival_time)}</div>
        <div class="ticket-route">{html_text(ticket.departure_city)} → {html_text(ticket.arrival_city)}</div>
      </div>
      <div class="ticket-price">￥{ticket.price}</div>
      <div class="ticket-row"><span class="label">出发时间</span><span class="value">{html_text(ticket.departure_time)}</span></div>
      <div class="ticket-row"><span class="label">到达时间</span><span class="value">{html_text(ticket.arrival_time)}{html_text(arrive_suffix)}</span></div>
      <div class="ticket-row"><span class="label">航班号</span><span class="value">{html_text(ticket.flight_numbers)}</span></div>
      <div class="ticket-row"><span class="label">航司名字</span><span class="value">{html_text(ticket.airlines)}</span></div>
      <div class="ticket-row"><span class="label">总耗时</span><span class="value">{html_text(ticket.total_duration or '未知')}</span></div>
      <div class="ticket-row"><span class="label">机场</span><span class="value">{html_text(ticket.departure_airport)} → {html_text(ticket.arrival_airport)}</span></div>
      <div class="ticket-row"><span class="label">类型</span><span class="value">{html_text(ticket.flight_type)}</span></div>
      <div class="table-title">价格对比表</div>
      <table class="price-table">
        <thead>
          <tr><th>日期</th><th>价格</th><th>增幅</th></tr>
        </thead>
        <tbody>
          {table_html}
        </tbody>
      </table>
    </article>
    """.strip()


def build_single_ticket_html(
    title: str,
    now: datetime,
    route: dict[str, Any],
    ticket: Ticket,
    curve: list[dict[str, Any]] | None,
    source_url: str,
) -> str:
    arrive_suffix = f" 到达日期 {ticket.arrival_date}" if ticket.arrival_day_offset > 0 else ""
    curve_rows = normalize_price_table(curve)
    curve_table = "".join(
        [
            f"<tr><td>{html_text(row['time'])}</td><td>￥{row['price']}</td><td>{html_text(row['change'])}</td></tr>"
            for row in curve_rows
        ]
    ) or "<tr><td colspan='3'>暂无历史波动</td></tr>"
    expected_price = route.get("expected_price")
    expected_text = f"≤￥{int(expected_price)}" if expected_price not in (None, "") else "不限"
    html_doc = f"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>{html_text(title)}</title>
      <style>
        :root{{--bg:#f6f8fc;--card:#fff;--line:#e6ebf5;--text:#18212f;--muted:#667085;--brand:#2563eb;--accent:#f97316;--shadow:0 12px 30px rgba(15,23,42,.08)}}
        html,body{{width:100%;margin:0;padding:0}}
        *{{box-sizing:border-box}}body{{background:linear-gradient(180deg,#edf4ff 0%,var(--bg) 32%,var(--bg) 100%);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
        .wrap{{width:100%;max-width:980px;margin:0 auto;padding:12px 12px 22px}}
        .hero{{background:linear-gradient(135deg,#1d4ed8,#2563eb 58%,#60a5fa);color:#fff;border-radius:24px;padding:20px;box-shadow:var(--shadow)}}
        .hero h1{{margin:0;font-size:40px;line-height:1.05;letter-spacing:.5px}}
        .hero .route{{margin-top:8px;font-size:28px;font-weight:800}}
        .hero .sub{{margin-top:8px;font-size:13px;opacity:.95}}
        .layout{{display:flex;flex-direction:column;gap:14px;margin-top:14px}}
        .card{{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:16px;box-shadow:var(--shadow)}}
        .price{{font-size:48px;font-weight:900;color:var(--accent);line-height:1}}
        .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 14px;margin-top:14px}}
        .item{{padding:10px 0;border-bottom:1px dashed var(--line)}}
        .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
        .value{{display:block;font-size:16px;font-weight:700;word-break:break-word}}
        .table-title{{margin:0 0 10px;font-size:13px;font-weight:800;color:#16a34a}}
        table{{width:100%;border-collapse:collapse;font-size:12px}}
        th,td{{padding:7px 4px;border-bottom:1px solid var(--line);text-align:left}}
        th{{color:var(--muted)}}
        .meta-list{{display:grid;gap:10px}}
        .meta-pill{{display:inline-block;padding:7px 10px;border-radius:999px;background:#eef4ff;color:var(--brand);font-size:12px;font-weight:700;margin-right:8px;margin-bottom:8px}}
        .footer{{margin-top:14px;font-size:12px;color:var(--muted)}}
        .footer a{{color:var(--brand);text-decoration:none}}
        @media (max-width:760px){{.wrap{{padding:8px 8px 18px}}.hero,.card{{border-radius:18px;padding:14px}}.grid{{grid-template-columns:1fr 1fr}}.hero h1{{font-size:28px}}.hero .route{{font-size:20px}}.price{{font-size:34px}}}}
        @media (max-width:520px){{.grid{{grid-template-columns:1fr}}}}
      </style>
    </head>
    <body>
      <div class="wrap">
        <section class="hero">
          <h1>{html_text(ticket.departure_time)} → {html_text(ticket.arrival_time)}</h1>
          <div class="route">{html_text(route['departure_city'])} → {html_text(route['arrival_city'])}｜{html_text(ticket.departure_date)}</div>
          <div class="sub">推送时间：{html_text(now.strftime('%Y-%m-%d %H:%M:%S'))}{html_text(arrive_suffix)}</div>
        </section>
        <section class="layout">
          <article class="card">
            <div class="price">￥{ticket.price}</div>
            <div class="grid">
              <div class="item"><span class="label">出发时间</span><span class="value">{html_text(ticket.departure_time)}</span></div>
              <div class="item"><span class="label">到达时间</span><span class="value">{html_text(ticket.arrival_time)}</span></div>
              <div class="item"><span class="label">航班号</span><span class="value">{html_text(ticket.flight_numbers)}</span></div>
              <div class="item"><span class="label">航司名字</span><span class="value">{html_text(ticket.airlines)}</span></div>
              <div class="item"><span class="label">总耗时</span><span class="value">{html_text(ticket.total_duration or '未知')}</span></div>
              <div class="item"><span class="label">价格条件</span><span class="value">{html_text(expected_text)}</span></div>
              <div class="item"><span class="label">出发机场</span><span class="value">{html_text(ticket.departure_airport)}</span></div>
              <div class="item"><span class="label">到达机场</span><span class="value">{html_text(ticket.arrival_airport)}</span></div>
            </div>
          </article>
          <article class="card">
            <p class="table-title">价格对比表</p>
            <table>
              <thead><tr><th>日期</th><th>价格</th><th>增幅</th></tr></thead>
              <tbody>{curve_table}</tbody>
            </table>
            <div class="footer">
              <div><span class="meta-pill">{html_text(ticket.flight_type)}</span>{''.join(f"<span class='meta-pill'>{html_text(label)}</span>" for label in ticket.labels[:3])}</div>
<div>原始页面：<a href="{html_text(source_url)}">查看携程</a></div>
            </div>
          </article>
        </section>
      </div>
    </body>
    </html>
    """
    return minify_html(html_doc)


def build_route_notification_html(title: str, now: datetime, route_section: str) -> str:
    return build_pushplus_contents(title, now, [route_section], limit=99999999)[0]


def build_route_section(
    route: dict[str, Any],
    source_url: str,
    matched_tickets: list[Ticket],
    all_tickets: list[Ticket],
    history: dict[str, Any],
    config: dict[str, Any],
) -> str:
    expected_price = route.get("expected_price")
    expected_text = f"≤￥{int(expected_price)}" if expected_price not in (None, "") else "不限"
    display_tickets = matched_tickets if matched_tickets else all_tickets[:3]
    if matched_tickets:
        status_text = f"命中：{len(matched_tickets)} / {len(all_tickets)}"
        notice_html = ""
    elif display_tickets:
        status_text = f"未命中预期，展示最低 {len(display_tickets)} 条"
        notice_html = (
            "<div class='route-notice warn'>"
            "当前没有机票达到预期价格，以下展示该行程价格最低的 3 张机票。"
            "</div>"
        )
    else:
        status_text = "暂无可用机票"
        notice_html = "<div class='route-notice info'>当前没有抓取到可用机票数据。</div>"
    header_html = f"""
    <header class="route-header">
      <div>
        <div class="route-date">{html_text(route['departure_date'])}</div>
        <h2>{html_text(route['departure_city'])} → {html_text(route['arrival_city'])}</h2>
      </div>
      <div class="route-meta">
        <span>预期价格：{html_text(expected_text)}</span>
        <span>{html_text(status_text)}</span>
      </div>
    </header>
    """.strip()
    if not display_tickets:
        body_html = "<div class='empty-route'>当前没有抓取到可用机票。</div>"
    else:
        cards_html = "\n".join(
            [
                build_ticket_card_html(index, ticket, get_ticket_curve(history, config, route, ticket))
                for index, ticket in enumerate(display_tickets, start=1)
            ]
        )
        body_html = f"<div class='ticket-grid'>{cards_html}</div>"

    return f"""
    <section class="route-section">
      {header_html}
      {notice_html}
      {body_html}
<div class="route-source">原始页面：<a href="{html_text(source_url)}">查看携程</a></div>
    </section>
    """.strip()


def build_pushplus_contents(
    title: str,
    now: datetime,
    route_sections: list[str],
    limit: int = 18000,
) -> list[str]:
    def utf8_len(text: str) -> int:
        return len(text.encode("utf-8"))

    def wrap_html(body: str, part_index: int | None = None, total_parts: int | None = None) -> str:
        part_badge = (
            f"<div class='part-badge'>消息分片 {part_index}/{total_parts}</div>"
            if part_index is not None and total_parts is not None and total_parts > 1
            else ""
        )
        html_doc = f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>{html_text(title)}</title>
          <style>
            :root {{--line:#e8edf5;--text:#1f2937;--muted:#667085;--brand:#2563eb;--accent:#f97316;}}
            html,body {{width:100%;margin:0;padding:0}}
            * {{box-sizing:border-box}}
            body {{background:#f5f7fb;color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
            .wrap {{width:100%;max-width:980px;margin:0 auto;padding:12px 12px 24px}}
            .hero {{
              width:100%;margin:0 auto;background:#2563eb;color:#fff;border-radius:20px;padding:18px;
            }}
            .hero h1 {{margin:0 0 8px;font-size:32px;line-height:1.2}}
            .hero p {{margin:4px 0;font-size:13px;opacity:.94}}
            .part-badge {{margin-top:8px;display:inline-block;padding:5px 9px;border-radius:999px;background:rgba(255,255,255,.18);font-size:12px}}
            .route-section {{
              width:100%;margin:14px auto 0;background:#fff;border:1px solid var(--line);border-radius:20px;padding:14px;
            }}
            .route-header {{
              display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:12px;
            }}
            .route-header h2 {{margin:4px 0 0;font-size:28px}}
            .route-date {{font-size:12px;color:var(--brand);font-weight:700}}
            .route-meta {{
              display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;
            }}
            .route-meta span {{
              background:#eef4ff;color:var(--brand);padding:7px 10px;border-radius:999px;font-size:12px;font-weight:600;
            }}
            .route-source {{
              margin-top:12px;font-size:12px;color:var(--muted);
            }}
            .route-source a {{
              color:var(--brand);text-decoration:none;font-weight:700;
            }}
            .route-notice {{
              margin-bottom:12px;padding:10px 12px;border-radius:12px;font-size:13px;font-weight:600;
            }}
            .route-notice.warn {{
              background:#fff7ed;border:1px solid #fdba74;color:#9a3412;
            }}
            .route-notice.info {{
              background:#eff6ff;border:1px solid #93c5fd;color:#1d4ed8;
            }}
            .ticket-grid {{
              display:flex;flex-direction:column;gap:12px;width:100%;
            }}
            .ticket-card {{
              width:100%;max-width:100%;margin:0 auto;position:relative;background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;min-height:100%;
            }}
            .ticket-order {{position:absolute;top:12px;right:12px;font-size:12px;color:var(--muted)}}
            .ticket-main {{margin-bottom:10px;padding-right:32px}}
            .ticket-time {{font-size:32px;line-height:1.05;font-weight:800;color:var(--brand)}}
            .ticket-route {{margin-top:4px;font-size:15px;font-weight:700;color:var(--text)}}
            .ticket-price {{
              font-size:36px;line-height:1;font-weight:800;color:var(--accent);margin-bottom:12px;
            }}
            .ticket-row {{
              display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px dashed var(--line);
            }}
            .ticket-row .label {{color:var(--muted);font-size:12px;white-space:nowrap}}
            .ticket-row .value {{text-align:right;font-size:13px;font-weight:600;word-break:break-word}}
            .table-title {{
              margin-top:12px;margin-bottom:6px;font-size:12px;color:#16a34a;font-weight:700;
            }}
            .price-table {{
              width:100%;border-collapse:collapse;font-size:11px;
            }}
            .price-table th,
            .price-table td {{
              padding:4px 2px;border-bottom:1px solid var(--line);text-align:left;
            }}
            .price-table th {{color:var(--muted);font-weight:700}}
            .empty-route {{
              padding:18px;border-radius:12px;background:#f8fafc;border:1px dashed var(--line);color:var(--muted);
            }}
            @media (max-width: 640px) {{
              .wrap {{padding:8px 8px 18px}}
              .hero,.route-section,.ticket-card {{border-radius:14px;padding:12px}}
              .hero h1 {{font-size:24px}}
              .route-header h2 {{font-size:22px}}
              .ticket-time {{font-size:24px}}
              .ticket-price {{font-size:28px}}
              .route-meta {{justify-content:flex-start}}
              .ticket-row {{flex-direction:column;align-items:flex-start}}
              .ticket-row .value {{text-align:left}}
            }}
          </style>
        </head>
        <body>
          <div class="wrap">
            <section class="hero">
              <h1>{html_text(title)}</h1>
              <p>推送时间：{html_text(now.strftime('%Y-%m-%d %H:%M:%S'))}</p>
              <p>已按预期价格筛选，仅展示命中的机票。</p>
              {part_badge}
            </section>
            {body}
          </div>
        </body>
        </html>
        """.strip()
        return minify_html(html_doc)

    if not route_sections:
        return [
            wrap_html("<section class='route-section'><div class='empty-route'>当前没有低于预期价格的机票。</div></section>")
        ]

    grouped_blocks: list[list[str]] = []
    current_group: list[str] = []
    base_length = utf8_len(wrap_html(""))
    current_length = base_length

    for section in route_sections:
        extra = utf8_len(section) + utf8_len("\n\n")
        if current_group and current_length + extra > limit:
            grouped_blocks.append(current_group)
            current_group = [section]
            current_length = base_length + extra
            continue
        current_group.append(section)
        current_length += extra

    if current_group:
        grouped_blocks.append(current_group)

    contents: list[str] = []
    total_parts = len(grouped_blocks)
    for index, group in enumerate(grouped_blocks, start=1):
        contents.append(wrap_html("\n".join(group), index, total_parts))
    return contents


def send_pushplus(push_cfg: dict[str, Any], title: str, content: str) -> dict[str, Any]:
    payload = {
        "token": push_cfg["token"],
        "title": title,
        "content": content,
        "template": push_cfg.get("template", "html"),
        "channel": push_cfg.get("channel", "wechat"),
    }
    max_retries = int(push_cfg.get("retry_count", 5))
    for attempt in range(1, max_retries + 1):
        response = requests.post(PUSHPLUS_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if int(data.get("code", 0)) == 200:
            return data
        error_text = str(data.get("data") or data.get("msg") or "")
        if "推送频率过快" in error_text and attempt < max_retries:
            time.sleep(min(10, 2 * attempt))
            continue
        raise RuntimeError(f"PushPlus 推送失败: {data}")
    raise RuntimeError("PushPlus 推送失败：超过最大重试次数")


def send_pushplus_notifications(push_cfg: dict[str, Any], notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    interval_seconds = max(0, int(push_cfg.get("min_interval_seconds", 15)))
    for index, item in enumerate(notifications, start=1):
        results.append(send_pushplus(push_cfg, item["title"], item["html"]))
        if index < len(notifications) and interval_seconds:
            time.sleep(interval_seconds)
    return results


def build_onebot_route_message(
    route: dict[str, Any],
    matched_tickets: list[Ticket],
    all_tickets: list[Ticket],
    history: dict[str, Any],
    config: dict[str, Any],
) -> str:
    expected_price = route.get("expected_price")
    expected_text = f"≤￥{int(expected_price)}" if expected_price not in (None, "") else "不限"
    display_tickets = matched_tickets if matched_tickets else all_tickets[:3]
    lines = [
        f"{route['departure_date']} {route['departure_city']} → {route['arrival_city']}",
        f"预期价格：{expected_text}",
    ]
    if matched_tickets:
        lines.append(f"命中机票：{len(matched_tickets)} / {len(all_tickets)}")
    elif display_tickets:
        lines.append("当前没有机票达到预期价格，以下展示价格最低的 3 张机票。")
    else:
        lines.append("当前没有抓取到可用机票。")
        return "\n".join(lines)

    for index, ticket in enumerate(display_tickets, start=1):
        rows = normalize_price_table(get_ticket_curve(history, config, route, ticket))
        last_row = rows[-1] if rows else {"time": "暂无", "price": ticket.price, "change": "-"}
        arrive_suffix = f" {ticket.arrival_date}" if ticket.arrival_day_offset > 0 else ""
        lines.extend(
            [
                "",
                f"#{index} ￥{ticket.price}",
                f"时间：{ticket.departure_time} → {ticket.arrival_time}{arrive_suffix}",
                f"航班：{ticket.flight_numbers}",
                f"航司：{ticket.airlines}",
                f"机场：{ticket.departure_airport} → {ticket.arrival_airport}",
                f"耗时：{ticket.total_duration or '未知'}",
                f"最新波动：{last_row['time']} | ￥{last_row['price']} | {last_row['change']}",
            ]
        )
    return "\n".join(lines)


def send_onebot_messages(onebot_cfg: dict[str, Any], messages: list[str]) -> list[dict[str, Any]]:
    if not onebot_cfg.get("enabled"):
        return []
    base_url = str(onebot_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("onebot-config.json 已启用但未配置 base_url")
    headers = {"Content-Type": "application/json"}
    token = str(onebot_cfg.get("access_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    targets = onebot_cfg.get("targets") or []
    if not targets:
        raise ValueError("onebot-config.json 已启用但未配置 targets")

    results: list[dict[str, Any]] = []
    for message in messages:
        for target in targets:
            message_type = str(target.get("message_type") or "").strip().lower()
            if message_type == "private":
                endpoint = f"{base_url}/send_private_msg"
                payload = {"user_id": int(target["user_id"]), "message": message}
            elif message_type == "group":
                endpoint = f"{base_url}/send_group_msg"
                payload = {"group_id": int(target["group_id"]), "message": message}
            else:
                raise ValueError(f"onebot target.message_type 不支持: {message_type}")

            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            if str(data.get("status", "")).lower() == "failed" or int(data.get("retcode", 0)) not in (0, 1):
                raise RuntimeError(f"OneBot 推送失败: {data}")
            results.append(data)
    return results


def build_email_html(title: str, now: datetime, route_sections: list[str]) -> str:
    return build_pushplus_contents(title, now, route_sections, limit=99999999)[0]


def html_to_text(html_content: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", "", html_content, flags=re.IGNORECASE)
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def send_resend_email(email_cfg: dict[str, Any], subject: str, html_content: str, text_content: str) -> dict[str, Any]:
    api_key = str(email_cfg.get("api_key") or "").strip()
    sender = str(email_cfg.get("from") or "").strip()
    recipients = email_cfg.get("to") or []
    if not api_key or not sender or not recipients:
        raise ValueError("email 配置不完整：需要 api_key / from / to")

    payload = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }
    response = requests.post(
        "https://api.resend.com/emails",
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("id"):
        raise RuntimeError(f"Resend 邮件发送失败: {data}")
    return data


class CtripMonitor:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.browser_path = detect_browser_executable(config)
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.playwright = None
        self.browser_cfg = config["browser"]
        self.city_lookup: dict[str, dict[str, Any]] = {}
        cookie_file = Path(str(config.get("cookie_file") or DEFAULT_COOKIE_PATH))
        self.cookie_path = cookie_file
        self.cookies = load_cookie_file(cookie_file)
        self.http = build_ctrip_requests_session(self.cookies)

    async def __aenter__(self) -> "CtripMonitor":
        self.playwright = await async_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            launch_args.extend(["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
        self.browser = await self.playwright.chromium.launch(
            headless=bool(self.browser_cfg.get("headless", True)),
            executable_path=self.browser_path,
            args=launch_args,
        )
        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={
                "width": int(self.browser_cfg.get("viewport_width", 1280)),
                "height": int(self.browser_cfg.get("viewport_height", 720)),
            },
        )
        if self.cookies:
            await self.context.add_cookies(self.cookies)
            safe_output(f"已加载携程登录 Cookie：{self.cookie_path}（{len(self.cookies)} 条）")
        else:
            safe_output("未检测到 cookie.json，将以未登录状态抓取；若要抓取完整航班列表，请提供导出的携程登录 Cookie。")
        await self.context.add_init_script(INIT_SCRIPT)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.http.close()

    async def _load_city_lookup(self) -> None:
        if self.city_lookup:
            return
        if not self.context:
            raise RuntimeError("浏览器上下文未初始化")

        page = await self.context.new_page()
        try:
            await page.goto(CTRIP_HOME_URL, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(1500)
            await page.click(".flt-depart input", timeout=10000)
            await page.wait_for_timeout(800)
            tab_locator = page.locator(".city-picker-tabs").first.locator("li")
            tab_count = await tab_locator.count()
            lookup: dict[str, dict[str, Any]] = {}
            for idx in range(tab_count):
                await tab_locator.nth(idx).click()
                await page.wait_for_timeout(250)
                items = await page.locator(".city-picker-body .cities li").evaluate_all(
                    """els => els.map(el => ({
                        title: (el.getAttribute('title') || '').trim(),
                        remark: (el.getAttribute('data-u_remark') || '').trim()
                    }))"""
                )
                for item in items:
                    title = str(item.get("title") or "").strip()
                    remark = str(item.get("remark") or "").strip()
                    if not title or not remark:
                        continue
                    try:
                        info = parse_city_selector_remark(remark)
                    except ValueError:
                        continue
                    info["name"] = title
                    for alias in iter_city_aliases(title):
                        lookup[alias] = info
            if not lookup:
                raise RuntimeError("未能从携程页面解析城市列表")
            self.city_lookup = lookup
        finally:
            await page.close()

    def _resolve_city_info(self, city_name: str) -> dict[str, Any]:
        normalized = normalize_city_name(city_name)
        if normalized in self.city_lookup:
            return self.city_lookup[normalized]

        suggestions = dedupe_in_order(
            [
                info["name"]
                for alias, info in self.city_lookup.items()
                if normalized and (normalized in alias or alias in normalized)
            ]
        )
        suffix = f"；可尝试：{'、'.join(suggestions[:8])}" if suggestions else ""
        raise ValueError(f"未找到携程支持的城市：{city_name}{suffix}")

    async def prepare_route(self, route: dict[str, Any]) -> None:
        if route.get("source_url") and route.get("departure_city_code") and route.get("arrival_city_code"):
            return
        await self._load_city_lookup()
        departure = self._resolve_city_info(str(route.get("departure_city") or ""))
        arrival = self._resolve_city_info(str(route.get("arrival_city") or ""))
        route["departure_city"] = departure["name"]
        route["arrival_city"] = arrival["name"]
        route["departure_city_code"] = departure["code"]
        route["arrival_city_code"] = arrival["code"]
        route["departure_city_id"] = departure["city_id"]
        route["arrival_city_id"] = arrival["city_id"]
        route["source_url"] = build_ctrip_url(route)

    async def _handle_route(self, route) -> None:
        request = route.request
        resource_type = request.resource_type
        url = request.url.lower()

        if self.browser_cfg.get("block_images", True) and resource_type == "image":
            await route.abort()
            return
        if self.browser_cfg.get("block_fonts", True) and resource_type == "font":
            await route.abort()
            return
        if self.browser_cfg.get("block_media", True) and resource_type in {"media", "websocket"}:
            await route.abort()
            return
        if self.browser_cfg.get("block_stylesheets", True) and resource_type == "stylesheet":
            await route.abort()
            return

        await route.continue_()

    async def _extract_visible_prices(self, page: Page) -> list[dict[str, Any]]:
        return await page.evaluate(
            """() => {
                const clean = value => (value || '').replace(/[\\t\\r\\n]+/g, ' ').replace(/\\s+/g, ' ').trim();
                const text = (root, sel) => {
                  const el = root.querySelector(sel);
                  return el ? clean(el.textContent || '') : '';
                };
                const texts = (root, sel) => Array.from(root.querySelectorAll(sel)).map(el => clean(el.textContent || '')).filter(Boolean);
                const airportText = container => {
                  if (!container) return '';
                  return clean(text(container, '.airport .name') + text(container, '.airport .terminal'));
                };
                return Array.from(document.querySelectorAll('.flight-item.domestic')).map(card => {
                const fullText = clean(card.innerText || '');
                const upperText = fullText.toUpperCase();
                const flightNumbers = Array.from(new Set(upperText.match(/\\b[A-Z0-9]{2,3}\\d{3,4}\\b/g) || []));
                const price = text(card, '.flight-price .price').replace(/\\D+/g, '');
                const transferText = text(card, '.arrow-box [id^="transfer-text-"]');
                const daycross = text(card, '.arrive-box .day');
                return {
                  price,
                  airlines: text(card, '.airline-name span') || text(card, '.airline'),
                  flight_numbers: flightNumbers.join('/'),
                  departure_time: text(card, '.depart-box .time'),
                  arrival_time: text(card, '.arrive-box .time'),
                  departure_airport: airportText(card.querySelector('.depart-box')),
                  arrival_airport: airportText(card.querySelector('.arrive-box')),
                  range_text: '',
                  arrival_day_note: daycross,
                  transfer_city: '',
                  transfer_duration: '',
                  discount: text(card, '.sub-price-item'),
                  labels: texts(card, '.flight-tags .tag'),
                  flight_type: fullText.includes('经停') ? '经停' : ((flightNumbers.length > 1 || /中转|转机/.test(transferText)) ? '中转' : '直飞')
                };
            })}"""
        )

    async def _wait_for_flight_payload(self, page: Page) -> dict[str, Any]:
        timeout_ms = max(60000, int(self.browser_cfg.get("wait_timeout_ms", 25000)) * 2)
        poll_interval_ms = int(self.browser_cfg.get("poll_interval_ms", 500))
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        latest_payload: dict[str, Any] | None = None

        while asyncio.get_running_loop().time() < deadline:
            logs = await page.evaluate(
                f"""() => ((window.__flightMonitor && window.__flightMonitor.requests) || []).filter(
                    item => (item.url || '').includes('{CTRIP_BATCH_SEARCH_KEYWORD}')
                )"""
            )
            for item in sorted(logs, key=lambda row: row.get("ts", 0), reverse=True):
                try:
                    payload = json.loads(item["responseText"])
                except Exception:
                    continue
                latest_payload = payload
                if int(payload.get("status", -1)) == 0:
                    return payload
            await page.wait_for_timeout(poll_interval_ms)

        if latest_payload is not None:
            return latest_payload
        raise RuntimeError("未捕获到有效的携程 batchSearch 响应")

    async def _collect_display_prices(self, page: Page, target_count: int = 0) -> list[dict[str, Any]]:
        collected: dict[str, dict[str, Any]] = {}
        stale_rounds = 0
        for _ in range(20):
            rows = await self._extract_visible_prices(page)
            before = len(collected)
            for row in rows:
                if not str(row.get("price") or "").isdigit():
                    continue
                key = "|".join(
                    [
                        normalize_flight_numbers(row.get("flight_numbers", "")),
                        str(row.get("departure_time") or "").strip(),
                        str(row.get("arrival_time") or "").strip(),
                    ]
                )
                if key and key.count("|") == 2:
                    collected[key] = row
            if target_count and len(collected) >= target_count:
                break
            scroll_y = await page.evaluate("window.scrollY")
            max_scroll_y = await page.evaluate("Math.max(document.documentElement.scrollHeight - window.innerHeight, 0)")
            await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
            await page.wait_for_timeout(900)
            new_scroll_y = await page.evaluate("window.scrollY")
            if len(collected) == before and (new_scroll_y <= scroll_y or new_scroll_y >= max_scroll_y):
                stale_rounds += 1
                if stale_rounds >= 3:
                    break
            else:
                stale_rounds = 0
        return list(collected.values())

    async def _fetch_route_payload_once(self, route: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        if not self.context:
            raise RuntimeError("浏览器上下文未初始化")
        page = await self.context.new_page()
        url = str(route["source_url"])
        try:
            timeout_ms = max(60000, int(self.browser_cfg.get("wait_timeout_ms", 25000)) * 2)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            payload = await self._wait_for_flight_payload(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            target_count = len(payload.get("data", {}).get("flightItineraryList", []))
            display_prices = await self._collect_display_prices(page, target_count=target_count)
            return url, payload, display_prices
        finally:
            await page.close()

    async def fetch_route_payload(self, route: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        attempts = max(1, int(self.browser_cfg.get("request_retries", 3)))
        last_result: tuple[str, dict[str, Any], list[dict[str, Any]]] | None = None
        for attempt in range(1, attempts + 1):
            last_result = await self._fetch_route_payload_once(route)
            _, payload, display_prices = last_result
            if int(payload.get("status", -1)) == 0:
                return last_result
            if attempt < attempts:
                await asyncio.sleep(0.8 * attempt)
        assert last_result is not None
        return last_result


def route_cache_key(route: dict[str, Any]) -> str:
    return f"{route['departure_city']}|{route['arrival_city']}|{route['departure_date']}"


def ticket_curve_key(ticket: Ticket) -> str:
    return "|".join(
        [
            ticket.route,
            ticket.departure_date,
            ticket.flight_numbers,
            ticket.departure_time,
            ticket.arrival_time,
            ticket.departure_airport,
            ticket.arrival_airport,
        ]
    )


def normalize_flight_numbers(value: str) -> str:
    codes = re.findall(r"\b[A-Z0-9]{2,3}\d{3,4}\b", str(value or "").upper())
    return "/".join(codes)


def ticket_lookup_key(ticket: Ticket) -> str:
    return "|".join(
        [
            normalize_flight_numbers(ticket.flight_numbers),
            ticket.departure_time,
            ticket.arrival_time,
        ]
    )


def get_timezone(config: dict[str, Any]) -> ZoneInfo:
    timezone_name = config.get("service", {}).get("timezone", "Asia/Shanghai")
    return ZoneInfo(timezone_name)


def now_in_timezone(config: dict[str, Any]) -> datetime:
    return datetime.now(get_timezone(config))


def today_key(config: dict[str, Any]) -> str:
    return now_in_timezone(config).strftime("%Y-%m-%d")


def current_title(now: datetime) -> str:
    return now.strftime("%m月%d日 %H:%M 机票价格")


def ticket_notification_title(now: datetime, route: dict[str, Any], ticket: Ticket) -> str:
    return f"{now.strftime('%m月%d日')} {route['departure_city']}→{route['arrival_city']} {ticket.departure_date}"


def route_notification_title(now: datetime, route: dict[str, Any]) -> str:
    return f"{now.strftime('%m月%d日')} {route['departure_city']}→{route['arrival_city']} {route['departure_date']}"


def normalize_schedule_value(value: str) -> str:
    return value.strip()


def slot_key_for_datetime(slot_time: datetime, slot: str) -> str:
    return f"{slot_time.strftime('%Y-%m-%d')} {normalize_schedule_value(slot)}"


def current_slot_key(now: datetime, slot: str) -> str:
    return slot_key_for_datetime(now, slot)


def slot_to_datetime(now: datetime, slot: str) -> datetime:
    hour, minute = [int(part) for part in normalize_schedule_value(slot).split(":", 1)]
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def iter_schedule_occurrences(
    now: datetime,
    schedule_times: list[str],
    day_offsets: tuple[int, ...],
) -> list[ScheduleOccurrence]:
    occurrences: list[ScheduleOccurrence] = []
    for day_offset in day_offsets:
        base = now + timedelta(days=day_offset)
        for slot in schedule_times:
            normalized_slot = normalize_schedule_value(slot)
            slot_time = slot_to_datetime(base, normalized_slot)
            occurrences.append(
                ScheduleOccurrence(
                    slot=normalized_slot,
                    slot_time=slot_time,
                    slot_key=slot_key_for_datetime(slot_time, normalized_slot),
                )
            )
    occurrences.sort(key=lambda item: item.slot_time)
    return occurrences


def get_due_capture_slots(
    now: datetime,
    schedule_times: list[str],
    state: dict[str, Any],
    captured_slot_keys: set[str],
    capture_lead_seconds: int,
) -> list[ScheduleOccurrence]:
    due: list[ScheduleOccurrence] = []
    lead_seconds = max(0, int(capture_lead_seconds))
    for occurrence in iter_schedule_occurrences(now, schedule_times, (0, 1)):
        if occurrence.slot_key in captured_slot_keys or is_slot_sent(state, occurrence.slot_key):
            continue
        capture_time = occurrence.slot_time - timedelta(seconds=lead_seconds)
        if capture_time <= now < occurrence.slot_time:
            due.append(occurrence)
    return due


def get_due_slots(
    now: datetime,
    schedule_times: list[str],
    state: dict[str, Any],
    grace_seconds: int,
) -> list[ScheduleOccurrence]:
    due: list[ScheduleOccurrence] = []
    for occurrence in iter_schedule_occurrences(now, schedule_times, (-1, 0)):
        delta = (now - occurrence.slot_time).total_seconds()
        if 0 <= delta <= grace_seconds and not is_slot_sent(state, occurrence.slot_key):
            due.append(occurrence)
    return due


def seconds_until_next_schedule(now: datetime, schedule_times: list[str]) -> int:
    candidates: list[datetime] = []
    for slot in schedule_times:
        target = slot_to_datetime(now, slot)
        if target <= now:
            target = target + timedelta(days=1)
        candidates.append(target)
    if not candidates:
        return 3600
    delta = min(candidates) - now
    return max(1, int(delta.total_seconds()))


def seconds_until_next_capture(
    now: datetime,
    schedule_times: list[str],
    state: dict[str, Any],
    captured_slot_keys: set[str],
    capture_lead_seconds: int,
) -> int:
    lead_seconds = max(0, int(capture_lead_seconds))
    candidates: list[datetime] = []
    for occurrence in iter_schedule_occurrences(now, schedule_times, (0, 1)):
        if occurrence.slot_key in captured_slot_keys or is_slot_sent(state, occurrence.slot_key):
            continue
        capture_time = occurrence.slot_time - timedelta(seconds=lead_seconds)
        if capture_time <= now < occurrence.slot_time:
            return 1
        if capture_time > now:
            candidates.append(capture_time)
    if not candidates:
        return 3600
    delta = min(candidates) - now
    return max(1, int(delta.total_seconds()))


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"days": {}}
    try:
        data = load_json(path)
        if isinstance(data, dict):
            data.setdefault("days", {})
            return data
    except Exception:
        pass
    return {"days": {}}


def prune_history_days(history: dict[str, Any], keep_days: int) -> None:
    days = history.setdefault("days", {})
    if keep_days <= 0:
        return
    day_keys = sorted(days.keys())
    while len(day_keys) > keep_days:
        old_key = day_keys.pop(0)
        days.pop(old_key, None)


def save_history(path: Path, history: dict[str, Any]) -> None:
    save_json(path, history)


def ticket_summary_meta(ticket: Ticket) -> dict[str, Any]:
    return {
        "route": ticket.route,
        "departure_date": ticket.departure_date,
        "flight_type": ticket.flight_type,
        "airlines": ticket.airlines,
        "flight_numbers": ticket.flight_numbers,
        "departure_time": ticket.departure_time,
        "arrival_time": ticket.arrival_time,
        "departure_airport": ticket.departure_airport,
        "arrival_airport": ticket.arrival_airport,
    }


def update_price_history(
    history: dict[str, Any],
    config: dict[str, Any],
    route: dict[str, Any],
    tickets: list[Ticket],
    current_time: datetime,
) -> None:
    day = current_time.strftime("%Y-%m-%d")
    route_key = route_cache_key(route)
    day_data = history.setdefault("days", {}).setdefault(day, {})
    route_data = day_data.setdefault(route_key, {})

    for ticket in tickets:
        key = ticket_curve_key(ticket)
        entry = route_data.setdefault(
            key,
            {
                "meta": ticket_summary_meta(ticket),
                "changes": [],
            },
        )
        changes = entry.setdefault("changes", [])
        if not changes or int(changes[-1]["price"]) != ticket.price:
            changes.append(
                {
                    "time": current_time.strftime("%m-%d %H:%M"),
                    "price": ticket.price,
                }
            )
        entry["meta"] = ticket_summary_meta(ticket)
        entry["last_seen"] = current_time.strftime("%Y-%m-%d %H:%M:%S")

    keep_days = int(config.get("service", {}).get("history_retention_days", 7))
    prune_history_days(history, keep_days)


def get_ticket_curve(
    history: dict[str, Any],
    config: dict[str, Any],
    route: dict[str, Any],
    ticket: Ticket,
) -> list[dict[str, Any]]:
    day = today_key(config)
    return (
        history.get("days", {})
        .get(day, {})
        .get(route_cache_key(route), {})
        .get(ticket_curve_key(ticket), {})
        .get("changes", [])
    )


def filter_tickets_for_route(route: dict[str, Any], tickets: list[Ticket]) -> list[Ticket]:
    expected_price = route.get("expected_price")
    if expected_price in (None, ""):
        return tickets
    limit = int(expected_price)
    return [ticket for ticket in tickets if ticket.price <= limit]


def apply_display_row(ticket: Ticket, row: dict[str, Any]) -> None:
    if str(row.get("price") or "").isdigit():
        ticket.price = int(row["price"])
    airline = str(row.get("airlines") or "").strip()
    if airline:
        ticket.airlines = airline
    discount = str(row.get("discount") or "").strip()
    if discount:
        ticket.discount = discount
    labels = dedupe_in_order([str(item).strip() for item in (row.get("labels") or []) if str(item).strip()])
    if discount and discount not in labels:
        labels.insert(0, discount)
    if labels:
        ticket.labels = labels


def apply_display_prices(tickets: list[Ticket], display_rows: list[dict[str, Any]]) -> None:
    ordered_rows = [row for row in display_rows if str(row.get("price") or "").isdigit()]
    display_map: dict[str, dict[str, Any]] = {}
    for row in ordered_rows:
        key = "|".join(
            [
                normalize_flight_numbers(row.get("flight_numbers", "")),
                str(row.get("departure_time") or "").strip(),
                str(row.get("arrival_time") or "").strip(),
            ]
        )
        if key and key.count("|") == 2:
            display_map[key] = row

    for ticket in tickets:
        key = ticket_lookup_key(ticket)
        row = display_map.get(key)
        if row:
            apply_display_row(ticket, row)

    if len(ordered_rows) == len(tickets):
        for ticket, row in zip(tickets, ordered_rows):
            apply_display_row(ticket, row)
    tickets.sort(key=lambda item: (item.price, item.departure_time, item.arrival_time, item.flight_numbers))


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sent_slots": []}
    try:
        data = load_json(path)
        if isinstance(data, dict):
            data.setdefault("sent_slots", [])
            return data
    except Exception:
        pass
    return {"sent_slots": []}


def save_state_if_needed(path: Path, state: dict[str, Any]) -> None:
    save_json(path, state)


def content_changed(previous: str, current: str) -> bool:
    return previous != current


def cleanup_sent_slots(state: dict[str, Any], config: dict[str, Any]) -> None:
    now = now_in_timezone(config)
    valid_days = {
        (now + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (-1, 0)
    }
    state["sent_slots"] = [
        slot for slot in state.get("sent_slots", []) if str(slot).split(" ", 1)[0] in valid_days
    ]


def cleanup_runtime_slot_cache(
    now: datetime,
    slot_snapshots: dict[str, list[dict[str, Any]]],
    captured_slot_keys: set[str],
) -> None:
    valid_days = {
        (now + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in (-1, 0, 1)
    }
    for slot_key in list(slot_snapshots.keys()):
        if str(slot_key).split(" ", 1)[0] not in valid_days:
            slot_snapshots.pop(slot_key, None)
    captured_slot_keys.intersection_update(
        {
            slot_key
            for slot_key in captured_slot_keys
            if str(slot_key).split(" ", 1)[0] in valid_days
        }
    )


def is_slot_sent(state: dict[str, Any], slot_key: str) -> bool:
    return slot_key in state.get("sent_slots", [])


def mark_slot_sent(state: dict[str, Any], slot_key: str) -> None:
    sent_slots = state.setdefault("sent_slots", [])
    if slot_key not in sent_slots:
        sent_slots.append(slot_key)


def validate_runtime_config(config: dict[str, Any]) -> None:
    routes = config.get("routes", [])
    if not isinstance(routes, list) or not routes:
        raise ValueError("config.json 中 routes 不能为空")
    for index, route in enumerate(routes, start=1):
        if not isinstance(route, dict):
            raise ValueError(f"config.json 中第 {index} 条 route 必须是 JSON 对象")
        for field in ("departure_city", "arrival_city", "departure_date"):
            if not str(route.get(field) or "").strip():
                raise ValueError(f"config.json 中第 {index} 条 route 缺少字段: {field}")
    push_cfg = config["pushplus"]
    if not push_cfg.get("token"):
        raise ValueError("config.json 中 pushplus.token 不能为空")


async def collect_route_results(
    config: dict[str, Any],
    monitor: CtripMonitor,
    history: dict[str, Any],
    current_time: datetime,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for route in config["routes"]:
        if not route.get("enabled", True):
            continue
        await monitor.prepare_route(route)
        url, payload, display_rows = await monitor.fetch_route_payload(route)
        tickets, parser_mode = resolve_ctrip_tickets(route, payload, display_rows)
        if not tickets:
            repo_lowest_price: int | None = None
            try:
                repo_lowest_payload = fetch_ctrip_repo_lowest_price_payload(route, monitor.http)
            except requests.RequestException as exc:
                safe_output(
                    f"警告：参考仓库同源的 12808 日历价接口请求失败："
                    f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']} | {exc}"
                )
            else:
                repo_lowest_price = extract_ctrip_repo_lowest_price(route, repo_lowest_payload)

            try:
                lowest_price_payload = fetch_ctrip_lowest_price_payload(route, monitor.http)
            except requests.RequestException as exc:
                safe_output(
                    f"警告：携程日历最低价接口请求失败，仍按空结果处理："
                    f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']} | {exc}"
                )
            else:
                lowest_price_tickets = parse_ctrip_lowest_price_tickets(
                    route,
                    lowest_price_payload,
                    price_override=repo_lowest_price,
                )
                if lowest_price_tickets:
                    safe_output(
                        f"提示：航班列表为空，已改用携程日历最低价接口："
                        f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']}"
                    )
                    tickets = lowest_price_tickets
                    parser_mode = "lowest_price"
            if not tickets and repo_lowest_price is not None:
                safe_output(
                    f"提示：已改用参考仓库同源的 12808 日历价接口："
                    f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']}"
                )
                tickets = [build_ctrip_lowest_price_ticket(route, price=repo_lowest_price, extra_labels=["来自 12808 日历价接口"])]
                parser_mode = "lowest_price_12808"
        update_price_history(history, config, route, tickets, current_time)
        matched_tickets = filter_tickets_for_route(route, tickets)
        results.append(
            {
                "route": route,
                "url": url,
                "parser_mode": parser_mode,
                "display_price_count": len(display_rows),
                "tickets": tickets,
                "matched_tickets": matched_tickets,
                "ticket_count": len(tickets),
                "matched_count": len(matched_tickets),
            }
        )
    return results


def build_notification_items(
    config: dict[str, Any],
    route_results: list[dict[str, Any]],
    history: dict[str, Any],
    current_time: datetime,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    notify_empty_results = bool(config.get("notify_empty_results", True))
    for result in route_results:
        route = result["route"]
        matched_tickets = result["matched_tickets"]
        if not matched_tickets and not notify_empty_results:
            continue
        title = route_notification_title(current_time, route)
        route_section = build_route_section(
            route,
            result["url"],
            matched_tickets,
            result["tickets"],
            history,
            config,
        )
        html_content = build_route_notification_html(title, current_time, route_section)
        items.append(
            {
                "title": title,
                "route": route,
                "tickets": matched_tickets if matched_tickets else result["tickets"][:3],
                "html": html_content,
                "text": html_to_text(html_content),
                "onebot_message": build_onebot_route_message(route, matched_tickets, result["tickets"], history, config),
            }
        )
    return items


def print_route_results(route_results: list[dict[str, Any]], title: str) -> None:
    safe_output("=" * 80)
    safe_output(title)
    for result in route_results:
        route = result["route"]
        expected_price = route.get("expected_price")
        expected_text = f"≤￥{int(expected_price)}" if expected_price not in (None, "") else "不限"
        safe_output(
            f"{route['departure_city']} -> {route['arrival_city']} {route['departure_date']} | "
            f"全部 {result['ticket_count']} 条 | 符合预期 {result['matched_count']} 条 | 预期价 {expected_text}"
        )


def serialize_route_results(route_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for result in route_results:
        serialized.append(
            {
                "route": result["route"],
                "url": result["url"],
                "parser_mode": result.get("parser_mode", ""),
                "ticket_count": result["ticket_count"],
                "matched_count": result["matched_count"],
                "tickets": [asdict(ticket) for ticket in result["tickets"]],
                "matched_tickets": [asdict(ticket) for ticket in result["matched_tickets"]],
            }
        )
    return serialized


def push_contents(push_cfg: dict[str, Any], title: str, contents: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, content in enumerate(contents, start=1):
        part_title = title if len(contents) == 1 else f"{title}（{index}/{len(contents)}）"
        results.append(send_pushplus(push_cfg, part_title, content))
    return results


async def run_monitor(config: dict[str, Any], dry_run: bool, dump_json: bool) -> list[dict[str, Any]]:
    validate_runtime_config(config)
    history_file = Path(config.get("history_file", ".flight_monitor_history.json"))
    history = load_history(history_file)
    onebot_cfg = load_onebot_config()
    current_time = now_in_timezone(config)

    async with CtripMonitor(config) as monitor:
        route_results = await collect_route_results(config, monitor, history, current_time)

    summary_title = current_title(current_time)
    notification_items = build_notification_items(config, route_results, history, current_time)
    print_route_results(route_results, summary_title)

    should_push = bool(notification_items)
    push_results: list[dict[str, Any]] = []
    onebot_results: list[dict[str, Any]] = []
    email_results: list[dict[str, Any]] = []
    if should_push and not dry_run:
        push_results = send_pushplus_notifications(config["pushplus"], notification_items)
        if onebot_cfg.get("enabled") and notification_items:
            onebot_results = send_onebot_messages(onebot_cfg, [item["onebot_message"] for item in notification_items])
        email_cfg = config.get("email", {})
        if email_cfg.get("enabled"):
            email_results = [
                send_resend_email(email_cfg, item["title"], item["html"], item["text"])
                for item in notification_items
            ]
    elif not should_push:
        safe_output("当前没有低于预期价格的机票，已跳过推送。")

    preview = notification_items[0]["html"] if notification_items else ""
    if preview:
        safe_output(preview[:3000])
        if len(preview) > 3000:
            safe_output("\n...（内容过长，已截断输出）")

    if not dry_run:
        save_history(history_file, history)

    results = serialize_route_results(route_results)
    if dump_json:
        print(
            json.dumps(
                {
                    "title": summary_title,
                    "push_results": push_results,
                    "onebot_results": onebot_results,
                    "email_results": email_results,
                    "route_results": results,
                    "notification_count": len(notification_items),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return results


async def run_service(config: dict[str, Any], dry_run: bool, dump_json: bool) -> None:
    validate_runtime_config(config)
    service_cfg = config.get("service", {})
    onebot_cfg = load_onebot_config()
    email_cfg = config.get("email", {})
    capture_lead_minutes = max(0, int(service_cfg.get("capture_lead_minutes", 10)))
    capture_lead_seconds = capture_lead_minutes * 60
    sleep_cap = max(10, int(service_cfg.get("sleep_cap_seconds", 60)))
    grace_seconds = max(30, int(service_cfg.get("schedule_grace_seconds", 300)))
    schedule_times = [normalize_schedule_value(item) for item in service_cfg.get("schedule_times", ["09:00"])]

    state_file = Path(config.get("state_file", ".flight_monitor_state.json"))
    history_file = Path(config.get("history_file", ".flight_monitor_history.json"))
    state = load_state(state_file)
    history = load_history(history_file)
    cleanup_sent_slots(state, config)

    slot_snapshots: dict[str, list[dict[str, Any]]] = {}
    captured_slot_keys: set[str] = set()

    async with CtripMonitor(config) as monitor:
        while True:
            now = now_in_timezone(config)
            cleanup_sent_slots(state, config)
            cleanup_runtime_slot_cache(now, slot_snapshots, captured_slot_keys)

            due_capture_slots = get_due_capture_slots(
                now,
                schedule_times,
                state,
                captured_slot_keys,
                capture_lead_seconds,
            )
            if due_capture_slots:
                route_results = await collect_route_results(config, monitor, history, now)
                for occurrence in due_capture_slots:
                    slot_snapshots[occurrence.slot_key] = route_results
                    captured_slot_keys.add(occurrence.slot_key)
                print_route_results(route_results, f"{current_title(now)} pre-capture complete")
                if not dry_run:
                    save_history(history_file, history)

            due_slots = get_due_slots(now, schedule_times, state, grace_seconds)
            if due_slots:
                push_occurrence = due_slots[-1]
                route_results = slot_snapshots.get(push_occurrence.slot_key)
                if route_results is None:
                    safe_output(
                        f"Missing pre-capture snapshot for {push_occurrence.slot}; collecting once immediately before push."
                    )
                    route_results = await collect_route_results(config, monitor, history, now)
                    slot_snapshots[push_occurrence.slot_key] = route_results
                    captured_slot_keys.add(push_occurrence.slot_key)
                    print_route_results(route_results, f"{current_title(now)} fallback capture complete")
                    if not dry_run:
                        save_history(history_file, history)

                notification_items = build_notification_items(config, route_results, history, now)
                title = current_title(now)
                should_push = bool(notification_items)
                push_results: list[dict[str, Any]] = []
                onebot_results: list[dict[str, Any]] = []
                email_results: list[dict[str, Any]] = []
                if should_push and not dry_run:
                    push_results = send_pushplus_notifications(config["pushplus"], notification_items)
                    if onebot_cfg.get("enabled") and notification_items:
                        onebot_results = send_onebot_messages(
                            onebot_cfg,
                            [item["onebot_message"] for item in notification_items],
                        )
                    if email_cfg.get("enabled"):
                        email_results = []
                        for item in notification_items:
                            email_results.append(send_resend_email(email_cfg, item["title"], item["html"], item["text"]))
                elif not should_push:
                    safe_output(f"{title}: no tickets below the expected price, skipped push.")

                for occurrence in due_slots:
                    mark_slot_sent(state, occurrence.slot_key)
                    slot_snapshots.pop(occurrence.slot_key, None)
                    captured_slot_keys.discard(occurrence.slot_key)
                if not dry_run:
                    save_state_if_needed(state_file, state)

                if dump_json:
                    print(
                        json.dumps(
                            {
                                "title": title,
                                "due_slots": [occurrence.slot for occurrence in due_slots],
                                "push_results": push_results,
                                "onebot_results": onebot_results,
                                "email_results": email_results,
                                "route_results": serialize_route_results(route_results),
                                "notification_count": len(notification_items),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )

            sleep_now = now_in_timezone(config)
            next_capture_delay = seconds_until_next_capture(
                sleep_now,
                schedule_times,
                state,
                captured_slot_keys,
                capture_lead_seconds,
            )
            next_schedule_delay = seconds_until_next_schedule(sleep_now, schedule_times)
            sleep_seconds = max(10, min(sleep_cap, next_capture_delay, next_schedule_delay))
            await asyncio.sleep(sleep_seconds)

def main() -> int:
    args = parse_args()
    config = ensure_config(Path(args.config))
    try:
        if args.service:
            asyncio.run(run_service(config, dry_run=args.dry_run, dump_json=args.dump_json))
        else:
            asyncio.run(run_monitor(config, dry_run=args.dry_run, dump_json=args.dump_json))
        return 0
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"执行失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
