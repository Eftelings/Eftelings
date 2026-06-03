import httpx
import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"
CET = ZoneInfo("Europe/Berlin")

# Station name as it appears in the ppth (path) field of the XML
STATION_PATTERNS = {
    "8011160": "Berlin Hbf",
    "8000105": "Frankfurt(M)Hbf",
    "8000261": "München Hbf",
    "8000284": "Nürnberg Hbf",
    "8000207": "Köln Hbf",
}

ROUTES = [
    ("8000207", "8011160", "Köln Hbf",         "Berlin Hbf"),
    ("8000207", "8000105", "Köln Hbf",         "Frankfurt(M) Hbf"),
    ("8000105", "8011160", "Frankfurt(M) Hbf", "Berlin Hbf"),
    ("8000207", "8000261", "Köln Hbf",         "München Hbf"),
    ("8000284", "8011160", "Nürnberg Hbf",     "Berlin Hbf"),
    ("8011160", "8000207", "Berlin Hbf",        "Köln Hbf"),
    ("8000105", "8000207", "Frankfurt(M) Hbf", "Köln Hbf"),
    ("8011160", "8000105", "Berlin Hbf",        "Frankfurt(M) Hbf"),
    ("8000261", "8000207", "München Hbf",       "Köln Hbf"),
    ("8011160", "8000284", "Berlin Hbf",        "Nürnberg Hbf"),
]

LONG_DISTANCE = {"ICE", "IC", "EC"}


def _headers() -> dict:
    return {
        "DB-Client-Id": os.environ["DB_CLIENT_ID"],
        "DB-Api-Key":   os.environ["DB_API_KEY"],
        "Accept":       "application/xml",
    }


def _parse_time(pt: Optional[str]) -> Optional[str]:
    """YYMMDDHHmm → ISO datetime in CET"""
    if not pt or len(pt) < 10:
        return None
    try:
        dt = datetime.strptime(pt[:10], "%y%m%d%H%M")
        return dt.replace(tzinfo=CET).isoformat()
    except Exception:
        return None


def _train_key(tl: ET.Element) -> Optional[str]:
    c = tl.get("c", "").strip()
    n = tl.get("n", "").strip()
    return f"{c}_{n}" if c and n else None


async def _fetch_plan(client: httpx.AsyncClient, eva: str, date_str: str) -> List[ET.Element]:
    """Fetch hourly plan slices 05–23, return all stop elements."""
    stops: List[ET.Element] = []
    for hour in range(5, 24):
        try:
            resp = await client.get(
                f"{BASE_URL}/plan/{eva}/{date_str}/{hour:02d}",
                headers=_headers(),
            )
            if resp.status_code == 200 and resp.text.strip():
                root = ET.fromstring(resp.text)
                stops.extend(root.findall("s"))
        except Exception as e:
            logger.warning("Plan %s h%02d: %s", eva, hour, e)
        await asyncio.sleep(0.2)
    return stops


async def _fetch_changes(client: httpx.AsyncClient, eva: str) -> Dict[str, str]:
    """Fetch full changes, return {stop_id: actual_arrival_iso}."""
    try:
        resp = await client.get(f"{BASE_URL}/fchg/{eva}", headers=_headers())
        if resp.status_code != 200 or not resp.text.strip():
            return {}
        root = ET.fromstring(resp.text)
        result: Dict[str, str] = {}
        for stop in root.findall("s"):
            sid = stop.get("id")
            ar = stop.find("ar")
            if sid and ar is not None:
                ct = ar.get("ct")
                if ct:
                    parsed = _parse_time(ct)
                    if parsed:
                        result[sid] = parsed
        return result
    except Exception as e:
        logger.error("Changes %s: %s", eva, e)
        return {}


def _departures_toward(stops: List[ET.Element], to_id: str) -> Dict[str, dict]:
    """Index of ICE/IC departures from a station that pass through to_id."""
    pattern = STATION_PATTERNS.get(to_id, "")
    index: Dict[str, dict] = {}
    for stop in stops:
        tl = stop.find("tl")
        if tl is None or tl.get("c") not in LONG_DISTANCE:
            continue
        dp = stop.find("dp")
        if dp is None:
            continue
        if pattern and pattern not in dp.get("ppth", ""):
            continue
        key = _train_key(tl)
        if not key or key in index:
            continue
        index[key] = {
            "stop_id":           stop.get("id"),
            "planned_departure": _parse_time(dp.get("pt")),
            "category":          tl.get("c"),
            "number":            tl.get("n"),
        }
    return index


def _arrivals_from(stops: List[ET.Element]) -> Dict[str, dict]:
    """Index of all ICE/IC arrivals at a station."""
    index: Dict[str, dict] = {}
    for stop in stops:
        tl = stop.find("tl")
        if tl is None or tl.get("c") not in LONG_DISTANCE:
            continue
        ar = stop.find("ar")
        if ar is None:
            continue
        key = _train_key(tl)
        if not key or key in index:
            continue
        index[key] = {
            "stop_id":        stop.get("id"),
            "planned_arrival": _parse_time(ar.get("pt")),
        }
    return index


async def fetch_daily_journeys() -> List[Dict]:
    if not os.getenv("DB_CLIENT_ID") or not os.getenv("DB_API_KEY"):
        logger.error("DB_CLIENT_ID / DB_API_KEY fehlen — .env erstellen!")
        return []

    today     = date.today()
    date_str  = today.strftime("%y%m%d")   # YYMMDD  e.g. 260603
    today_str = today.isoformat()

    unique = set(r[0] for r in ROUTES) | set(r[1] for r in ROUTES)
    plans:   Dict[str, List[ET.Element]] = {}
    changes: Dict[str, Dict[str, str]]   = {}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for eva in unique:
            logger.info("Fetching plan for station %s …", eva)
            plans[eva] = await _fetch_plan(client, eva, date_str)
            await asyncio.sleep(0.5)

        for eva in unique:
            logger.info("Fetching changes for station %s …", eva)
            changes[eva] = await _fetch_changes(client, eva)
            await asyncio.sleep(0.3)

    all_journeys: List[Dict] = []

    for from_id, to_id, from_name, to_name in ROUTES:
        dep_idx = _departures_toward(plans[from_id], to_id)
        arr_idx = _arrivals_from(plans[to_id])
        chg     = changes.get(to_id, {})
        matched = 0

        for key, dep in dep_idx.items():
            arr = arr_idx.get(key)
            if not arr:
                continue

            planned_arr = arr["planned_arrival"]
            actual_arr  = chg.get(arr["stop_id"])

            delay_min: Optional[int] = None
            if planned_arr and actual_arr:
                try:
                    p = datetime.fromisoformat(planned_arr)
                    a = datetime.fromisoformat(actual_arr)
                    delay_min = round((a - p).total_seconds() / 60)
                except Exception:
                    pass

            all_journeys.append({
                "date":              today_str,
                "train_name":        f"{dep['category']} {dep['number']}",
                "route":             f"{from_name} → {to_name}",
                "from_station":      from_name,
                "from_station_id":   from_id,
                "to_station":        to_name,
                "to_station_id":     to_id,
                "planned_departure": dep["planned_departure"],
                "planned_arrival":   planned_arr,
                "actual_arrival":    actual_arr,
                "delay_minutes":     delay_min,
                "trip_id":           f"{key}_{from_id}_{today_str}",
            })
            matched += 1

        logger.info("Matched %d trains  %s → %s", matched, from_name, to_name)

    return all_journeys
