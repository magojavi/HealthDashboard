#!/usr/bin/env python3
"""
scripts/backfill-garmin-details.py

ONE-TIME USE. Your existing data/activities.json was built with only 7
basic fields per row. Garmin actually still has the full detail (GPS,
elevation min/max, speed, calories, etc.) for every one of those old
activities — it just wasn't captured originally. This script fetches your
COMPLETE Garmin history once and enriches every existing row in place,
matching by date + type + distance + duration (since old rows have no
Garmin activity ID to match on directly).

Unlike sync-garmin.py, this pages through your ENTIRE activity history —
expect it to take a few minutes and make many API calls. Run it locally or
as a one-off manual GitHub Action run, not on a schedule.

Usage:
  GARMIN_EMAIL=... GARMIN_PASSWORD=... python scripts/backfill-garmin-details.py
"""

import json
import os
import sys
import time

from garminconnect import Garmin

ACTIVITIES_PATH = os.environ.get("ACTIVITIES_JSON_PATH", "data/activities.json")
PAGE_SIZE = 100

TYPE_MAP = {
    "running": "run", "trail_running": "run", "track_running": "run",
    "treadmill_running": "run", "indoor_running": "run",
    "cycling": "bike", "road_biking": "bike", "mountain_biking": "bike",
    "gravel_cycling": "bike", "indoor_cycling": "bike", "virtual_ride": "bike",
    "cyclocross": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim", "pool_swimming": "swim",
    "hiking": "hike",
}


def map_activity_type(type_key):
    if not type_key:
        return "other"
    return TYPE_MAP.get(type_key.lower(), "other")


def fetch_all_activities(client):
    all_raw = []
    start = 0
    while True:
        batch = client.get_activities(start, PAGE_SIZE)
        if not batch:
            break
        all_raw.extend(batch)
        print(f"  fetched {len(all_raw)} activities so far...")
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(0.5)  # be gentle with Garmin's servers
    return all_raw


def extract_extra_fields(a):
    max_elevation = a.get("maxElevation")
    min_elevation = a.get("minElevation")
    elevation_loss = a.get("elevationLoss")
    start_lat = a.get("startLatitude")
    start_lon = a.get("startLongitude")
    end_lat = a.get("endLatitude")
    end_lon = a.get("endLongitude")
    avg_speed = a.get("averageSpeed")
    max_speed = a.get("maxSpeed")
    calories = a.get("calories")
    max_hr = a.get("maxHR")
    avg_cadence = (
        a.get("averageRunningCadenceInStepsPerMinute")
        or a.get("averageBikingCadenceInRevPerMinute")
        or a.get("averageSwimCadenceInStrokesPerMinute")
    )
    activity_id = a.get("activityId")
    activity_name = a.get("activityName") or None

    return [
        round(max_elevation, 1) if max_elevation is not None else None,
        round(min_elevation, 1) if min_elevation is not None else None,
        int(round(elevation_loss)) if elevation_loss else None,
        round(start_lat, 6) if start_lat is not None else None,
        round(start_lon, 6) if start_lon is not None else None,
        round(end_lat, 6) if end_lat is not None else None,
        round(end_lon, 6) if end_lon is not None else None,
        round(avg_speed * 3.6, 2) if avg_speed else None,
        round(max_speed * 3.6, 2) if max_speed else None,
        int(round(calories)) if calories else None,
        int(round(max_hr)) if max_hr else None,
        round(avg_cadence, 1) if avg_cadence is not None else None,
        activity_id,
        activity_name,
    ]


def fingerprint_raw(a):
    type_key = (a.get("activityType") or {}).get("typeKey")
    mapped_type = map_activity_type(type_key)
    activity_date = (a.get("startTimeLocal") or "")[:10]
    distance_km = round((a.get("distance") or 0) / 1000, 1)
    duration_min = round((a.get("duration") or 0) / 60, 1)
    return (activity_date, mapped_type, distance_km, duration_min)


def fingerprint_row(row):
    date_, type_, dist, dur = row[0], row[1], row[2], row[3]
    dist_r = round(dist, 1) if dist is not None else None
    dur_r = round(dur, 1) if dur is not None else None
    return (date_, type_, dist_r, dur_r)


def main():
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print("Missing GARMIN_EMAIL / GARMIN_PASSWORD environment variables.", file=sys.stderr)
        sys.exit(1)

    with open(ACTIVITIES_PATH, "r", encoding="utf-8") as f:
        activities = json.load(f)
    print(f"Loaded {len(activities)} existing activities.")

    print("Logging into Garmin Connect...")
    client = Garmin(email, password)
    client.login()
    print("Logged in. Fetching full activity history (this can take a few minutes)...")

    raw_all = fetch_all_activities(client)
    print(f"Fetched {len(raw_all)} activities total from Garmin.")

    by_fingerprint = {}
    for a in raw_all:
        by_fingerprint[fingerprint_raw(a)] = a

    matched, unmatched = 0, 0
    enriched = []
    for row in activities:
        # pad row to at least 7 fields (original schema) before appending extras
        base = row[:7] + [None] * max(0, 7 - len(row))
        fp = fingerprint_row(base)
        raw = by_fingerprint.get(fp)
        if raw:
            extras = extract_extra_fields(raw)
            matched += 1
        else:
            extras = [None] * 14
            unmatched += 1
        enriched.append(base + extras)

    print(f"Matched {matched} / {len(activities)} existing rows to full Garmin detail.")
    print(f"Unmatched (kept as-is, extras left null): {unmatched}")

    with open(ACTIVITIES_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False)
    print(f"Wrote enriched data back to {ACTIVITIES_PATH}.")


if __name__ == "__main__":
    main()
