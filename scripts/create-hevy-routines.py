#!/usr/bin/env python3
"""
Create Hevy routines from data/hevy_routines.json via the Hevy Public API.

MODE=dryrun (default): resolve every exercise to a template id and print the
plan — nothing is written to Hevy.
MODE=create: create missing custom templates, then POST each routine whose
title doesn't already exist. Existing routines are never modified.

Requires HEVY_API_KEY (GitHub secret). The key is never printed.
"""
import json, os, sys, time, unicodedata
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "https://api.hevyapp.com/v1"
KEY = os.environ.get("HEVY_API_KEY")
MODE = os.environ.get("MODE", "dryrun")
SPEC = Path(__file__).resolve().parent.parent / "data" / "hevy_routines.json"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = Request(f"{API}{path}", data=data, method=method,
                  headers={"api-key": KEY, "Accept": "application/json",
                           "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as r:
                txt = r.read().decode()
                if not txt.strip(): return {}
                try: return json.loads(txt)
                except ValueError: return {"id": txt.strip().strip('"')}
        except HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            print(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:500]}")
            raise


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").split())


def paged(path, key, size):
    out, page = [], 1
    while True:
        d = call("GET", f"{path}?page={page}&pageSize={size}")
        out += d.get(key, [])
        if page >= d.get("page_count", page): return out
        page += 1


def main():
    if not KEY:
        sys.exit("HEVY_API_KEY not set")
    spec = json.loads(SPEC.read_text())

    templates = paged("/exercise_templates", "exercise_templates", 100)
    by_title = {}
    for t in templates:
        by_title.setdefault(norm(t["title"]), t)
    print(f"Templates: {len(templates)} ({sum(1 for t in templates if t.get('is_custom'))} custom)")

    # Template ids actually used in his workouts (most reliable match)
    used = {}
    for w in paged("/workouts", "workouts", 10):
        for ex in w.get("exercises", []):
            used.setdefault(norm(ex.get("title")), ex.get("exercise_template_id"))
    print(f"Distinct exercises in workout history: {len(used)}")

    routines_live = paged("/routines", "routines", 10)
    existing = {norm(r["title"]) for r in routines_live}
    rid_by_title = {norm(r["title"]): r["id"] for r in routines_live}
    print(f"Existing routines: {len(existing)}")

    ok = True
    for r in spec["routines"]:
        print(f"\n=== {r['title']} ===" + (("  [EXISTE → se ACTUALIZA]" if r.get("update") else "  [YA EXISTE → se omite]") if norm(r["title"]) in existing else ""))
        for ex in r["exercises"]:
            tid, src = None, None
            for cand in ex["match"]:
                n = norm(cand)
                if n in used and used[n]:
                    tid, src = used[n], f"historial '{cand}'"; break
                if n in by_title:
                    tid, src = by_title[n]["id"], f"plantilla '{by_title[n]['title']}' ({by_title[n].get('type')})"; break
            ex["_tid"] = tid
            if tid:
                print(f"  OK   {ex['match'][0]:<45} -> {tid}  [{src}]")
            elif ex.get("custom"):
                print(f"  NEW  {ex['match'][0]:<45} -> crear custom {ex['custom']['exercise_type']}/{ex['custom']['equipment_category']}")
                words = [w for w in norm(ex["match"][0]).replace("(", " ").replace(")", " ").replace("+", " ").split() if len(w) > 3]
                sugg = [t["title"] for t in templates if sum(w in norm(t["title"]) for w in words) >= 2][:8]
                if sugg: print(f"         sugerencias: {sugg}")
            else:
                print(f"  FAIL {ex['match'][0]} — no encontrado y sin definición custom"); ok = False

    if MODE != "create":
        print("\nDry-run: nada escrito en Hevy."); return
    if not ok:
        sys.exit("Hay ejercicios sin resolver; abortando.")

    created_custom = {}
    for r in spec["routines"]:
        is_update = norm(r["title"]) in existing
        if is_update and not r.get("update"):
            continue
        exercises = []
        for ex in r["exercises"]:
            tid = ex["_tid"]
            if not tid:
                c = ex["custom"]; k = norm(c["title"])
                if k not in created_custom:
                    res = call("POST", "/exercise_templates", {"exercise": c})
                    created_custom[k] = str(res.get("id") or res.get("exercise_template", {}).get("id"))
                    print(f"Custom creado: {c['title']} -> {created_custom[k]}")
                tid = created_custom[k]
            exercises.append({
                "exercise_template_id": tid, "superset_id": None,
                "rest_seconds": ex.get("rest_seconds"), "notes": ex.get("notes") or None,
                "sets": [{"type": "normal", **s} for s in ex["sets"]],
            })
        if is_update:
            rid0 = rid_by_title[norm(r["title"])]
            res = call("PUT", f"/routines/{rid0}", {"routine": {"title": r["title"],
                                                     "notes": r.get("notes"), "exercises": exercises}})
            print(f"Rutina ACTUALIZADA: {r['title']}")
            continue
        res = call("POST", "/routines", {"routine": {"title": r["title"], "folder_id": None,
                                                     "notes": r.get("notes"), "exercises": exercises}})
        rid = res.get("id") or (res.get("routine") or [{}])
        print(f"Rutina creada: {r['title']} -> {json.dumps(rid)[:120]}")


if __name__ == "__main__":
    main()
