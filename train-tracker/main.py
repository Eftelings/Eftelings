import logging
from contextlib import asynccontextmanager
from datetime import date as date_type

from dotenv import load_dotenv
load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from database import (
    get_all_journeys,
    get_dates_with_data,
    get_fahrgastrechte_delays,
    get_top_delays,
    has_data_for_today,
    init_db,
    save_journeys,
    set_submitted,
)
from tracker import fetch_daily_journeys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Berlin")


async def run_fetch():
    logger.info("Fetching train data from DB HAFAS API...")
    journeys = await fetch_daily_journeys()
    count = save_journeys(journeys)
    logger.info("Stored/updated %d journeys", count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Every 30 minutes between 6:00 and 23:30
    scheduler.add_job(run_fetch, "cron", hour="6-23", minute="0,30", id="regular_fetch")
    scheduler.start()

    if not has_data_for_today():
        logger.info("No data for today yet — fetching now...")
        await run_fetch()

    yield

    scheduler.shutdown()


app = FastAPI(title="Zug-Tracker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/top-delays")
async def api_top_delays(
    target_date: str = Query(default=None, alias="date"),
    limit: int = Query(default=5, ge=1, le=20),
):
    d = target_date or str(date_type.today())
    return {"date": d, "delays": get_top_delays(d, limit)}


@app.get("/api/journeys")
async def api_journeys(target_date: str = Query(default=None, alias="date")):
    d = target_date or str(date_type.today())
    return {"date": d, "journeys": get_all_journeys(d)}


@app.get("/api/dates")
async def api_dates():
    return get_dates_with_data()


@app.post("/api/refresh")
async def api_refresh():
    await run_fetch()
    return {"status": "ok"}


@app.get("/api/fahrgastrechte")
async def api_fahrgastrechte():
    return {"delays": get_fahrgastrechte_delays()}


@app.post("/api/fahrgastrechte/{journey_id}/submitted")
async def api_set_submitted(journey_id: int, submitted: bool = Query(default=True)):
    set_submitted(journey_id, submitted)
    return {"status": "ok"}
