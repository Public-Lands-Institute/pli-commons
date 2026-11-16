#!/usr/bin/env python3
"""Set the |date= field of PLI file pages on Commons to the capture date in
pli/data/photos.json (d, from EXIF via sync_photos) wherever it differs.
Older uploads carried the upload date or file mtime instead (get_image_date
fallback in upload_next.py).

Run:  python3 fix_commons_dates.py --dry-run   # list changes, edit nothing
      python3 fix_commons_dates.py             # edit file pages
"""
import os, re, sys, json, time
from pathlib import Path
from upload_next import load_env, make_session, COMMONS_API

PHOTOS = Path(__file__).resolve().parent.parent / "pli" / "data" / "photos.json"
DATE_RE = re.compile(r"(\|\s*date\s*=)([^\n|]*)")


def fetch_wikitext(session, files):
    out = {}
    for i in range(0, len(files), 50):
        for attempt in range(8):
            r = session.post(COMMONS_API, data={
                "action": "query", "format": "json", "formatversion": 2,
                "prop": "revisions", "rvprop": "content", "rvslots": "main",
                "titles": "|".join("File:" + f for f in files[i:i + 50])})
            if r.status_code == 200:
                break
            time.sleep(15 * (attempt + 1))
        else:
            sys.exit(f"ERROR: Commons API returned {r.status_code} repeatedly")
        for pg in r.json()["query"]["pages"]:
            if "revisions" in pg:
                out[pg["title"][5:]] = pg["revisions"][0]["slots"]["main"]["content"]
        time.sleep(2)
    return out


def main():
    dry = "--dry-run" in sys.argv
    photos = json.load(open(PHOTOS))
    want = {x["f"]: x["d"] for s in photos for x in photos[s]
            if x.get("commons_n") and x.get("f") and x.get("d")}

    if dry:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "PLI-maintenance/1.0"
    else:
        load_env()
        user = os.environ.get("COMMONS_USERNAME")
        bot = os.environ.get("COMMONS_BOT_NAME")
        pw = os.environ.get("COMMONS_BOT_PASSWORD")
        if not user or not pw:
            sys.exit("ERROR: COMMONS_USERNAME and COMMONS_BOT_PASSWORD must be set in .env")
        session, token = make_session(f"{user}@{bot}" if bot else user, pw)

    texts = fetch_wikitext(session, sorted(want))
    todo = []
    for f, d in sorted(want.items()):
        t = texts.get(f)
        if t is None:
            print(f"  MISSING on Commons: {f}")
            continue
        m = DATE_RE.search(t)
        if not m:
            print(f"  NO date field: {f}")
            continue
        if m.group(2).strip() != d:
            todo.append((f, m.group(2).strip(), d, t))

    print(f"{len(todo)} file pages need a date change")
    for n, (f, old, new, t) in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {f}: {old} -> {new}")
        if dry:
            continue
        r = session.post(COMMONS_API, data={
            "action": "edit", "title": f"File:{f}", "format": "json",
            "text": DATE_RE.sub(lambda m: m.group(1) + new, t, count=1),
            "summary": "Set date to capture date (EXIF DateTimeOriginal); was upload date",
            "nocreate": 1, "bot": 1, "token": token})
        res = r.json()
        if res.get("edit", {}).get("result") != "Success":
            print(f"      ERROR: {res}")
        time.sleep(1)


if __name__ == "__main__":
    main()
