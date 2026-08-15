#!/usr/bin/env python3
"""
scripts/sync-garmin.py

Pulls recent activities and sleep data from Garmin Connect (via the
unofficial `garminconnect` package — there is no official public API for
individuals) and merges anything new into:

  data/activities.json  -> [date, type, distance_km, duration_min, avg_hr,
                             elevation_gain_m, location, max_elevation_m,
                             min_elevation_m, elevation_loss_m, start_lat,
                             start_lon, end_lat, end_lon, avg_speed_kmh,
                             max_speed_kmh, calories, max_hr, avg_cadence,
                             activity_id, activity_name]
  data/sleep.json        -> [date, deep_min, light_min, awake_min, total_sleep_min]
  data/yearly.json       -> {year: {type: {km, h, n}}}  (always fully recomputed from activities.json)

The first 7 fields keep their original positions for backward compatibility
with anything already reading this file; everything from max_elevation_m
onward is new. Rows synced before this version won't have the new fields
(they'll be null) unless you run the one-off backfill script separately.

Only pulls a recent lookback window each run (not your whole history) to
stay fast and avoid hammering Garmin's servers. That's enough to catch
anything your watch synced late.

Requires: GARMIN_EMAIL / GARMIN_PASSWORD environment variables.
Requires: pip install garminconnect

NOTE: garminconnect relies on Garmin's undocumented internal API. If Garmin
changes something, this can break — check garminconnect's GitHub issues
first if it suddenly stops working.
"""

import json
import os
import sys
from datetime import date, timedelta

from garminconnect import Garmin

ACTIVITIES_PATH = os.environ.get("ACTIVITIES_JSON_PATH", "data/activities.json")
SLEEP_PATH = os.environ.get("SLEEP_JSON_PATH", "data/sleep.json")
YEARLY_PATH = os.environ.get("YEARLY_JSON_PATH", "data/yearly.json")

ACTIVITY_LOOKBACK_COUNT = 60   # most recent N activities to re-check each run
SLEEP_LOOKBACK_DAYS = 10       # re-check sleep for the last N days each run

TYPE_MAP = {
    "running": "run",
    "trail_running": "run",
    "track_running": "run",
    "treadmill_running": "run",
    "indoor_running": "run",
    "cycling": "bike",
    "road_biking": "bike",
    "mountain_biking": "bike",
    "gravel_cycling": "bike",
    "indoor_cycling": "bike",
    "virtual_ride": "bike",
    "cyclocross": "bike",
    "lap_swimming": "swim",
    "open_water_swimming": "swim",
    "pool_swimming": "swim",
    "hiking": "hike",
}


def map_activity_type(type_key):
    if not type_key:
        return "other"
    return TYPE_MAP.get(type_key.lower(), "other")


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def round_or_none(v, ndigits=2):
    return None if v is None else round(v, ndigits)


def activity_fingerprint(row):
    # Match on date+type+distance+duration for every row, old or new. Using
    # the Garmin activity ID only for freshly-fetched rows (and not for
    # legacy rows that don't have one) caused legacy and newly-synced
    # copies of the same activity to get different fingerprints and both
    # get kept — duplicating history. The fuzzy key alone is reliable
    # enough since duration is also matched, not just distance.
    date_, type_, dist, dur = row[0], row[1], row[2], row[3]
    dist_r = round(dist, 1) if dist is not None else None
    dur_r = round(dur, 1) if dur is not None else None
    return (date_, type_, dist_r, dur_r)


def fetch_recent_activities(client):
    raw = client.get_activities(0, ACTIVITY_LOOKBACK_COUNT)
    rows = []
    for a in raw:
        type_key = (a.get("activityType") or {}).get("typeKey")
        mapped_type = map_activity_type(type_key)

        start_local = a.get("startTimeLocal") or ""
        activity_date = start_local[:10]
        if not activity_date:
            continue

        distance_m = a.get("distance")
        distance_km = round_or_none(distance_m / 1000, 2) if distance_m else 0

        duration_s = a.get("duration")
        duration_min = round_or_none(duration_s / 60, 1) if duration_s else 0

        avg_hr = a.get("averageHR")
        avg_hr = int(round(avg_hr)) if avg_hr else None

        elevation_gain = a.get("elevationGain")
        elevation_gain = int(round(elevation_gain)) if elevation_gain else None

        location = a.get("locationName") or None

        max_elevation = a.get("maxElevation")
        max_elevation = round(max_elevation, 1) if max_elevation is not None else None

        min_elevation = a.get("minElevation")
        min_elevation = round(min_elevation, 1) if min_elevation is not None else None

        elevation_loss = a.get("elevationLoss")
        elevation_loss = int(round(elevation_loss)) if elevation_loss else None

        start_lat = a.get("startLatitude")
        start_lon = a.get("startLongitude")
        end_lat = a.get("endLatitude")
        end_lon = a.get("endLongitude")
        start_lat = round(start_lat, 6) if start_lat is not None else None
        start_lon = round(start_lon, 6) if start_lon is not None else None
        end_lat = round(end_lat, 6) if end_lat is not None else None
        end_lon = round(end_lon, 6) if end_lon is not None else None

        avg_speed = a.get("averageSpeed")  # m/s
        avg_speed_kmh = round(avg_speed * 3.6, 2) if avg_speed else None

        max_speed = a.get("maxSpeed")  # m/s
        max_speed_kmh = round(max_speed * 3.6, 2) if max_speed else None

        calories = a.get("calories")
        calories = int(round(calories)) if calories else None

        max_hr = a.get("maxHR")
        max_hr = int(round(max_hr)) if max_hr else None

        # cadence field name differs by sport (running vs cycling)
        avg_cadence = (
            a.get("averageRunningCadenceInStepsPerMinute")
            or a.get("averageBikingCadenceInRevPerMinute")
            or a.get("averageSwimCadenceInStrokesPerMinute")
        )
        avg_cadence = round(avg_cadence, 1) if avg_cadence is not None else None

        activity_id = a.get("activityId")
        activity_name = a.get("activityName") or None

        rows.append([
            activity_date, mapped_type, distance_km, duration_min, avg_hr,
            elevation_gain, location,
            max_elevation, min_elevation, elevation_loss,
            start_lat, start_lon, end_lat, end_lon,
            avg_speed_kmh, max_speed_kmh, calories, max_hr, avg_cadence,
            activity_id, activity_name,
        ])
    return rows


def fetch_recent_sleep(client):
    rows = []
    today = date.today()
    for i in range(SLEEP_LOOKBACK_DAYS):
        d = today - timedelta(days=i)
        d_str = d.isoformat()
        try:
            data = client.get_sleep_data(d_str)
        except Exception as e:
            print(f"  (no sleep data for {d_str}: {e})")
            continue

        dto = (data or {}).get("dailySleepDTO") or {}
        deep_s = dto.get("deepSleepSeconds") or 0
        light_s = dto.get("lightSleepSeconds") or 0
        rem_s = dto.get("remSleepSeconds") or 0
        awake_s = dto.get("awakeSleepSeconds") or 0

        if not (deep_s or light_s or rem_s or awake_s):
            continue  # no real sleep data for this date

        deep_min = round(deep_s / 60)
        # REM gets folded into "light" to match this repo's existing schema
        # (older rows predate Garmin reporting REM separately).
        light_min = round((light_s + rem_s) / 60)
        awake_min = round(awake_s / 60)
        total_min = deep_min + light_min - awake_min

        rows.append([d_str, deep_min, light_min, awake_min, total_min])
    return rows


def merge_activities(existing, new_rows):
    by_key = {}
    for r in existing:
        k = activity_fingerprint(r)
        if k not in by_key or len(r) > len(by_key[k]):
            by_key[k] = r

    added, upgraded = 0, 0
    for r in new_rows:
        k = activity_fingerprint(r)
        if k not in by_key:
            by_key[k] = r
            added += 1
        elif len(r) > len(by_key[k]):
            by_key[k] = r  # replace thin legacy row with the richer synced one
            upgraded += 1

    merged = sorted(by_key.values(), key=lambda r: r[0])
    return merged, added, upgraded


def merge_sleep(existing, new_rows):
    existing_by_date = {r[0]: r for r in existing}
    added, updated = 0, 0
    for r in new_rows:
        if r[0] not in existing_by_date:
            added += 1
        elif existing_by_date[r[0]] != r:
            updated += 1
        existing_by_date[r[0]] = r
    merged = sorted(existing_by_date.values(), key=lambda r: r[0])
    return merged, added, updated


def recompute_yearly(activities):
    yearly = {}
    for date_, type_, dist, dur, *_ in activities:
        if type_ not in ("run", "bike", "swim", "hike"):
            continue
        year = date_[:4]
        yearly.setdefault(year, {})
        bucket = yearly[year].setdefault(type_, {"km": 0, "h": 0, "n": 0})
        bucket["km"] = round(bucket["km"] + (dist or 0), 2)
        bucket["h"] = round(bucket["h"] + (dur or 0) / 60, 2)
        bucket["n"] += 1
    return dict(sorted(yearly.items()))


def main():
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print("Missing GARMIN_EMAIL / GARMIN_PASSWORD environment variables.", file=sys.stderr)
        sys.exit(1)

    print("Logging into Garmin Connect...")
    client = Garmin(email, password)
    client.login()
    print("Logged in.")

    print(f"Fetching last {ACTIVITY_LOOKBACK_COUNT} activities...")
    new_activities = fetch_recent_activities(client)
    print(f"  fetched {len(new_activities)} activities")

    print(f"Fetching sleep data for the last {SLEEP_LOOKBACK_DAYS} days...")
    new_sleep = fetch_recent_sleep(client)
    print(f"  fetched {len(new_sleep)} nights of sleep data")

    existing_activities = load_json(ACTIVITIES_PATH, [])
    merged_activities, n_added, n_upgraded = merge_activities(existing_activities, new_activities)
    print(f"Activities: {n_added} new row(s), {n_upgraded} legacy row(s) upgraded with fuller detail "
          f"({len(existing_activities)} -> {len(merged_activities)})")

    existing_sleep = load_json(SLEEP_PATH, [])
    merged_sleep, s_added, s_updated = merge_sleep(existing_sleep, new_sleep)
    print(f"Sleep: {s_added} new night(s), {s_updated} updated ({len(existing_sleep)} -> {len(merged_sleep)})")

    yearly = recompute_yearly(merged_activities)

    save_json(ACTIVITIES_PATH, merged_activities)
    save_json(SLEEP_PATH, merged_sleep)
    save_json(YEARLY_PATH, yearly)
    print("Done.")


if __name__ == "__main__":
    main()
