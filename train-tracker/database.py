import os
import sqlite3
from datetime import date as date_type
from typing import List, Dict

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith("postgres")

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DB_PATH = "trains.db"


def get_conn():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def init_db():
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS train_journeys (
                        id SERIAL PRIMARY KEY,
                        date TEXT NOT NULL,
                        train_name TEXT NOT NULL,
                        route TEXT NOT NULL,
                        from_station TEXT NOT NULL,
                        from_station_id TEXT,
                        to_station TEXT NOT NULL,
                        to_station_id TEXT,
                        planned_departure TEXT,
                        planned_arrival TEXT,
                        actual_arrival TEXT,
                        delay_minutes INTEGER,
                        trip_id TEXT,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(date, trip_id)
                    )
                """)
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS train_journeys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    train_name TEXT NOT NULL,
                    route TEXT NOT NULL,
                    from_station TEXT NOT NULL,
                    from_station_id TEXT,
                    to_station TEXT NOT NULL,
                    to_station_id TEXT,
                    planned_departure TEXT,
                    planned_arrival TEXT,
                    actual_arrival TEXT,
                    delay_minutes INTEGER,
                    trip_id TEXT,
                    last_updated TEXT DEFAULT (datetime('now')),
                    UNIQUE(date, trip_id)
                )
            """)
        conn.commit()
    finally:
        conn.close()


def save_journeys(journeys: List[Dict]) -> int:
    if not journeys:
        return 0
    count = 0
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor() as cur:
                for j in journeys:
                    try:
                        cur.execute("""
                            INSERT INTO train_journeys
                            (date, train_name, route, from_station, from_station_id,
                             to_station, to_station_id, planned_departure, planned_arrival,
                             actual_arrival, delay_minutes, trip_id, last_updated)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT(date, trip_id) DO UPDATE SET
                                actual_arrival  = EXCLUDED.actual_arrival,
                                delay_minutes   = EXCLUDED.delay_minutes,
                                last_updated    = CURRENT_TIMESTAMP
                        """, (
                            j["date"], j["train_name"], j["route"],
                            j["from_station"], j.get("from_station_id"),
                            j["to_station"], j.get("to_station_id"),
                            j["planned_departure"], j["planned_arrival"],
                            j["actual_arrival"], j["delay_minutes"], j["trip_id"],
                        ))
                        count += 1
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error(f"DB save error (Postgres): {e}")
        else:
            for j in journeys:
                try:
                    conn.execute("""
                        INSERT INTO train_journeys
                        (date, train_name, route, from_station, from_station_id,
                         to_station, to_station_id, planned_departure, planned_arrival,
                         actual_arrival, delay_minutes, trip_id, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(date, trip_id) DO UPDATE SET
                            actual_arrival  = excluded.actual_arrival,
                            delay_minutes   = excluded.delay_minutes,
                            last_updated    = datetime('now')
                    """, (
                        j["date"], j["train_name"], j["route"],
                        j["from_station"], j.get("from_station_id"),
                        j["to_station"], j.get("to_station_id"),
                        j["planned_departure"], j["planned_arrival"],
                        j["actual_arrival"], j["delay_minutes"], j["trip_id"],
                    ))
                    count += 1
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"DB save error (SQLite): {e}")
        conn.commit()
    finally:
        conn.close()
    return count


def get_top_delays(target_date: str, limit: int = 5) -> List[Dict]:
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM train_journeys
                    WHERE date = %s AND delay_minutes IS NOT NULL
                    ORDER BY delay_minutes DESC
                    LIMIT %s
                """, (target_date, limit))
                return [dict(r) for r in cur.fetchall()]
        else:
            rows = conn.execute("""
                SELECT * FROM train_journeys
                WHERE date = ? AND delay_minutes IS NOT NULL
                ORDER BY delay_minutes DESC
                LIMIT ?
            """, (target_date, limit)).fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_journeys(target_date: str) -> List[Dict]:
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM train_journeys
                    WHERE date = %s
                    ORDER BY
                        CASE WHEN delay_minutes IS NULL THEN 1 ELSE 0 END,
                        delay_minutes DESC,
                        route,
                        planned_departure
                """, (target_date,))
                return [dict(r) for r in cur.fetchall()]
        else:
            rows = conn.execute("""
                SELECT * FROM train_journeys
                WHERE date = ?
                ORDER BY
                    CASE WHEN delay_minutes IS NULL THEN 1 ELSE 0 END,
                    delay_minutes DESC,
                    route,
                    planned_departure
            """, (target_date,)).fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


def has_data_for_today() -> bool:
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM train_journeys WHERE date = %s",
                    (str(date_type.today()),)
                )
                count = cur.fetchone()[0]
        else:
            count = conn.execute(
                "SELECT COUNT(*) FROM train_journeys WHERE date = ?",
                (str(date_type.today()),)
            ).fetchone()[0]
        return count > 0
    finally:
        conn.close()


def get_dates_with_data() -> List[str]:
    conn = get_conn()
    try:
        if IS_POSTGRES:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT date FROM train_journeys ORDER BY date DESC LIMIT 30"
                )
                rows = cur.fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT date FROM train_journeys ORDER BY date DESC LIMIT 30"
            ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
