#!/usr/bin/env python3
"""
Sync Hevy workout data into data/strength.json for the HealthDashboard repo.

Fetches every workout from the Hevy Public API (https://api.hevyapp.com/docs/)
and writes them, mapped to a flat schema, into data/strength.json at the repo
root — same pattern as sync-garmin.py / sync-renpho.mjs, just a different
source and a strength-training-shaped schema instead of the cardio array format.

Requires the HEVY_API_KEY environment variable (set it as a GitHub Actions
secret, never hardcode it here).

NOTE: field names below (title, start_time, weight_kg, etc.) reflect the
publicly documented Hevy API schema. If Hevy has changed anything since,
a first manual run (via workflow_dispatch) will make that obvious in the
Action log or in a malformed strength.json — check the output once before
trusting the nightly cron.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = "https://api.hevyapp.com/v1"
API_KEY = os.environ.get("HEVY_API_KEY")
OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "strength.json"
PAGE_SIZE = 10  # Hevy's documented max page size for /workouts; lower this if the API rejects it
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def api_get(path, params=None):
    """GET a Hevy API endpoint and return the parsed JSON body."""
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}{path}{query}"
    req = Request(url, headers={"api-key": API_KEY, "Accept": "application/json"})

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            last_error = e
            if e.code == 429 and attempt < MAX_RETRIES:
                print(f"Rate limited (429), retrying in {RETRY_DELAY_SECONDS * attempt}s...")
                time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue
            raise
        except URLError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise
    raise last_error


def fetch_all_workouts():
    """Page through /v1/workouts and return the full list of raw workout objects."""
    if not API_KEY:
        print("ERROR: HEVY_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    workouts = []
    page = 1
    while True:
        data = api_get("/workouts", {"page": page, "pageSize": PAGE_SIZE})
        batch = data.get("workouts", [])
        workouts.extend(batch)

        page_count = data.get("page_count", page)
        print(f"Fetched page {page}/{page_count} ({len(batch)} workouts)")
        if page >= page_count or not batch:
            break
        page += 1

    return workouts


def summarize_exercise(exercise):
    """Reduce one exercise's sets down to totals plus the raw set list."""
    sets = exercise.get("sets", [])
    total_volume_kg = 0.0
    for s in sets:
        weight = s.get("weight_kg") or 0
        reps = s.get("reps") or 0
        total_volume_kg += weight * reps

    return {
        "name": exercise.get("title"),
        "sets": [
            {
                "type": s.get("type"),
                "weight_kg": s.get("weight_kg"),
                "reps": s.get("reps"),
                "distance_meters": s.get("distance_meters"),
                "duration_seconds": s.get("duration_seconds"),
                "rpe": s.get("rpe"),
            }
            for s in sets
        ],
        "total_volume_kg": round(total_volume_kg, 1),
    }


def transform(workout):
    """Map one raw Hevy workout into the flat shape stored in strength.json."""
    exercises = [summarize_exercise(e) for e in workout.get("exercises", [])]
    total_volume_kg = round(sum(e["total_volume_kg"] for e in exercises), 1)

    start = workout.get("start_time")
    end = workout.get("end_time")
    duration_min = None
    if start and end:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                start_dt = datetime.strptime(start, fmt)
                end_dt = datetime.strptime(end, fmt)
                duration_min = round((end_dt - start_dt).total_seconds() / 60, 1)
                break
            except ValueError:
                continue

    return {
        "hevy_id": workout.get("id"),
        "date": (start or "")[:10],
        "title": workout.get("title"),
        "start_time": start,
        "end_time": end,
        "duration_min": duration_min,
        "exercise_count": len(exercises),
        "total_volume_kg": total_volume_kg,
        "exercises": exercises,
    }


def main():
    raw_workouts = fetch_all_workouts()
    print(f"Total workouts fetched: {len(raw_workouts)}")

    transformed = [transform(w) for w in raw_workouts]
    transformed.sort(key=lambda w: w["date"])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(transformed, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(transformed)} workouts to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
