#!/usr/bin/env python3
"""
Actualiza data/activities.json, data/sleep.json y data/yearly.json
a partir de la API no oficial de Garmin Connect (paquete `garminconnect`).

Variables de entorno requeridas (se configuran como Secrets en GitHub Actions):
  GARMIN_EMAIL    - email de tu cuenta Garmin Connect
  GARMIN_PASSWORD - contraseña de tu cuenta Garmin Connect

Uso:
  pip install garminconnect
  GARMIN_EMAIL=... GARMIN_PASSWORD=... python3 sync_garmin.py
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TYPE_MAP = {
    "running": "run", "treadmill_running": "run",
    "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim", "swimming": "swim",
    "hiking": "hike",
}
SKIP_TYPES = {"multi_sport", "transition_v2"}


def load_json(name):
    path = DATA_DIR / name
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(name, data):
    path = DATA_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  wrote {path} ({path.stat().st_size} bytes)")


def main():
    try:
        from garminconnect import Garmin
    except ImportError:
        sys.exit("Falta el paquete garminconnect. Instálalo con: pip install garminconnect")

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Define GARMIN_EMAIL y GARMIN_PASSWORD como variables de entorno / secrets.")

    print("Conectando a Garmin Connect...")
    client = Garmin(email, password)
    client.login()

    activities_master = load_json("activities.json") or []
    existing_dates = {tuple(r) for r in activities_master}

    # Solo miramos los últimos 45 días: de sobra para no perdernos nada
    # aunque el job falle un par de ejecuciones seguidas.
    since = datetime.now(timezone.utc) - timedelta(days=45)
    print(f"Descargando actividades desde {since.date()}...")

    new_rows = []
    start = 0
    limit = 100
    while True:
        batch = client.get_activities(start, limit)
        if not batch:
            break
        stop = False
        for a in batch:
            begin_ts = a.get("beginTimestamp") or 0
            dt = datetime.fromtimestamp(begin_ts / 1000, tz=timezone.utc) if begin_ts else None
            if dt and dt < since:
                stop = True
                break
            raw_type = (a.get("activityType") or {}).get("typeKey")
            if raw_type in SKIP_TYPES:
                continue
            sport = TYPE_MAP.get(raw_type, "other")
            date = dt.strftime("%Y-%m-%d") if dt else None
            km = round((a.get("distance") or 0) / 100000, 2)
            minutes = round((a.get("duration") or 0) / 60000, 1)
            hr = a.get("averageHR") or a.get("avgHr")
            elev = a.get("elevationGain")
            elev = round(elev / 100, 0) if elev else None
            loc = a.get("locationName")
            row = [date, sport, km, minutes, int(hr) if hr else None, elev, loc]
            new_rows.append(row)
        if stop or len(batch) < limit:
            break
        start += limit

    added = 0
    for row in new_rows:
        key = tuple(row)
        if key not in existing_dates:
            activities_master.append(row)
            existing_dates.add(key)
            added += 1
    activities_master.sort(key=lambda r: r[0] or "")
    print(f"Actividades nuevas añadidas: {added}")
    save_json("activities.json", activities_master)

    print("Descargando sueño de los últimos 45 días...")
    sleep_master = load_json("sleep.json") or []
    sleep_by_date = {r[0]: r for r in sleep_master}
    d = since.date()
    today = datetime.now(timezone.utc).date()
    while d <= today:
        try:
            s = client.get_sleep_data(d.isoformat())
            dto = (s or {}).get("dailySleepDTO", {})
            deep = (dto.get("deepSleepSeconds") or 0) / 60
            light = (dto.get("lightSleepSeconds") or 0) / 60
            awake = (dto.get("awakeSleepSeconds") or 0) / 60
            total = deep + light
            if total > 0:
                sleep_by_date[d.isoformat()] = [d.isoformat(), round(deep, 1), round(light, 1), round(awake, 1), round(total, 1)]
        except Exception as e:
            print(f"  aviso: no se pudo leer sueño de {d}: {e}")
        d += timedelta(days=1)
    sleep_master = [sleep_by_date[k] for k in sorted(sleep_by_date)]
    save_json("sleep.json", sleep_master)

    print("Recalculando agregados por año (yearly.json)...")
    yearly = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0]))
    for row in activities_master:
        date, sport, km, minutes = row[0], row[1], row[2] or 0, row[3] or 0
        if not date:
            continue
        y = date[:4]
        yearly[y][sport][0] += km
        yearly[y][sport][1] += minutes / 60
        yearly[y][sport][2] += 1
    result = {}
    for y in sorted(yearly):
        result[y] = {}
        for sport in ("run", "bike", "swim", "hike"):
            v = yearly[y].get(sport)
            if v and (v[2] or sport in ("run", "bike", "swim")):
                result[y][sport] = {"km": round(v[0], 1), "h": round(v[1], 1), "n": v[2]}
    save_json("yearly.json", result)

    print("Listo.")


if __name__ == "__main__":
    main()
