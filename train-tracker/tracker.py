import httpx
import asyncio
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://v5.db.transport.rest"
CET = ZoneInfo("Europe/Berlin")

# (from_id, to_id, from_name, to_name)
ROUTES = [
    ("8000207", "8011160", "Köln Hbf",          "Berlin Hbf"),
    ("8000207", "8000105", "Köln Hbf",          "Frankfurt(M) Hbf"),
    ("8000105", "8011160", "Frankfurt(M) Hbf",  "Berlin Hbf"),
    ("8000207", "8000261", "Köln Hbf",          "München Hbf"),
    ("8000284", "8011160", "Nürnberg Hbf",      "Berlin Hbf"),
    # Gegenrichtungen
    ("8011160", "8000207", "Berlin Hbf",        "Köln Hbf"),
    ("8000105", "8000207", "Frankfurt(M) Hbf",  "Köln Hbf"),
    ("8011160", "8000105", "Berlin Hbf",        "Frankfurt(M) Hbf"),
    ("8000261", "8000207", "München Hbf",       "Köln Hbf"),
    ("8011160", "8000284", "Berlin Hbf",        "Nürnberg Hbf"),
]

LONG_DISTANCE_PREFIXES = ("ICE", "IC ", "EC ")


async def fetch_daily_journeys() -> List[Dict]:
    today = date.today()
    departure_dt = datetime(today.year, today.month, today.day, 5, 0, tzinfo=CET)
    all_journeys: List[Dict] = []

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for from_id, to_id, from_name, to_name in ROUTES:
            try:
                journeys = await _fetch_route(
                    client, from_id, to_id, from_name, to_name, departure_dt
                )
                all_journeys.extend(journeys)
                logger.info("Fetched %d trains  %s → %s", len(journeys), from_name, to_name)
            except Exception as e:
                logger.error("Error fetching %s → %s: %s", from_name, to_name, e)
            await asyncio.sleep(0.5)  # be polite to the public API

    return all_journeys


async def _fetch_route(
    client: httpx.AsyncClient,
    from_id: str,
    to_id: str,
    from_name: str,
    to_name: str,
    departure_dt: datetime,
    results: int = 8,
) -> List[Dict]:
    response = await client.get(
        f"{BASE_URL}/journeys",
        params={
            "from":       from_id,
            "to":         to_id,
            "departure":  departure_dt.isoformat(),
            "results":    results,
            "stopovers":  "false",
            "transfers":  0,
        },
    )

    if response.status_code != 200:
        logger.error("API %s for %s→%s: %s", response.status_code, from_name, to_name, response.text[:200])
        return []

    data = response.json()
    today_str = date.today().isoformat()
    journeys: List[Dict] = []

    for journey in data.get("journeys", []):
        legs = journey.get("legs", [])
        # Only direct connections (single leg)
        if len(legs) != 1:
            continue

        leg = legs[0]
        line = leg.get("line") or {}
        train_name: str = line.get("name", "")

        if not any(train_name.startswith(p) for p in LONG_DISTANCE_PREFIXES):
            continue

        planned_dep: Optional[str] = leg.get("plannedDeparture")
        planned_arr: Optional[str] = leg.get("plannedArrival")
        actual_arr:  Optional[str] = leg.get("arrival")
        arr_delay_sec                = leg.get("arrivalDelay")  # seconds | null

        # Only store trains that depart today
        if not planned_dep or not planned_dep.startswith(today_str):
            continue

        trip_id: Optional[str] = leg.get("tripId")
        if not trip_id:
            continue

        delay_min: Optional[int] = None
        if arr_delay_sec is not None:
            delay_min = round(arr_delay_sec / 60)

        journeys.append({
            "date":            today_str,
            "train_name":      train_name,
            "route":           f"{from_name} → {to_name}",
            "from_station":    from_name,
            "from_station_id": from_id,
            "to_station":      to_name,
            "to_station_id":   to_id,
            "planned_departure": planned_dep,
            "planned_arrival":   planned_arr,
            "actual_arrival":    actual_arr,
            "delay_minutes":     delay_min,
            "trip_id":           trip_id,
        })

    return journeys
