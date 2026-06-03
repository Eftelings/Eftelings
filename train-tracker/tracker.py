import httpx
import asyncio
import logging
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/fahrplan/v1"
CET = ZoneInfo("Europe/Berlin")

DESTINATION_KEYWORDS = {
    "8011160": "berlin",
    "8000105": "frankfurt",
    "8000261": "münchen",
    "8000284": "nürnberg",
    "8000207": "köln",
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

LONG_DISTANCE = ("ICE", "IC ", "EC ")


def _headers() -> dict:
    return {
        "DB-Client-Id": os.environ["DB_CLIENT_ID"],
        "DB-Api-Key":   os.environ["DB_API_KEY"],
        "Accept":       "application/json",
    }


def _parse_dt(time_str: Optional[str], date_str: Optional[str]) -> Optional[str]:
    if not time_str or not date_str:
        return None
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=CET).isoformat()
    except Exception:
        return None


async def fetch_daily_journeys() -> List[Dict]:
    if not os.getenv("DB_CLIENT_ID") or not os.getenv("DB_API_KEY"):
        logger.error("DB_CLIENT_ID / DB_API_KEY fehlen — siehe .env.example")
        return []

    today = date.today()
    all_journeys: List[Dict] = []

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for from_id, to_id, from_name, to_name in ROUTES:
            try:
                journeys = await _fetch_route(client, from_id, to_id, from_name, to_name, today)
                all_journeys.extend(journeys)
                logger.info("Fetched %d trains  %s → %s", len(journeys), from_name, to_name)
            except Exception as e:
                logger.error("Error %s → %s: %s", from_name, to_name, e)
            await asyncio.sleep(0.5)

    return all_journeys


async def _fetch_route(client, from_id, to_id, from_name, to_name, travel_date) -> List[Dict]:
    today_str = travel_date.isoformat()
    dest_kw = DESTINATION_KEYWORDS.get(to_id, "")

    resp = await client.get(
        f"{BASE_URL}/departureBoard/{from_id}",
        params={"date": today_str, "time": "05:00"},
        headers=_headers(),
    )

    if resp.status_code != 200:
        logger.error("API %s departures %s: %s", resp.status_code, from_name, resp.text[:200])
        return []

    data = resp.json()
    departures = data if isinstance(data, list) else data.get("Departure", [])

    journeys = []
    seen: set = set()

    for dep in departures:
        name = dep.get("name", "")
        direction = dep.get("direction", "").lower()

        if not any(name.startswith(p) for p in LONG_DISTANCE):
            continue
        if dest_kw and dest_kw not in direction:
            continue

        details_id = dep.get("detailsId", "")
        if not details_id or details_id in seen:
            continue
        seen.add(details_id)

        try:
            journey = await _get_journey(
                client, details_id, from_id, to_id, name, from_name, to_name, today_str
            )
            if journey:
                journeys.append(journey)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error("Journey detail error %s: %s", name, e)

    return journeys


async def _get_journey(
    client, details_id, from_id, to_id, train_name,
    from_name, to_name, today_str,
) -> Optional[Dict]:

    resp = await client.get(
        f"{BASE_URL}/journeyDetails/{details_id}",
        headers=_headers(),
    )

    if resp.status_code != 200:
        return None

    data = resp.json()
    stops = data if isinstance(data, list) else data.get("stops", data.get("Stop", []))

    origin = next((s for s in stops if s.get("id") == from_id), None)
    dest   = next((s for s in stops if s.get("id") == to_id),   None)

    if not origin or not dest:
        return None

    planned_dep = _parse_dt(origin.get("depTime"), origin.get("depDate") or today_str)
    planned_arr = _parse_dt(dest.get("arrTime"),   dest.get("arrDate")   or today_str)

    rt_arr_time = dest.get("rtArrTime")
    rt_arr_date = dest.get("rtArrDate") or dest.get("arrDate") or today_str
    actual_arr  = _parse_dt(rt_arr_time, rt_arr_date) if rt_arr_time else None

    delay_min: Optional[int] = None
    if planned_arr and actual_arr:
        try:
            p = datetime.fromisoformat(planned_arr)
            a = datetime.fromisoformat(actual_arr)
            delay_min = round((a - p).total_seconds() / 60)
        except Exception:
            pass

    return {
        "date":              today_str,
        "train_name":        train_name,
        "route":             f"{from_name} → {to_name}",
        "from_station":      from_name,
        "from_station_id":   from_id,
        "to_station":        to_name,
        "to_station_id":     to_id,
        "planned_departure": planned_dep,
        "planned_arrival":   planned_arr,
        "actual_arrival":    actual_arr,
        "delay_minutes":     delay_min,
        "trip_id":           details_id,
    }
