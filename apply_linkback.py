#!/usr/bin/env python3
"""
apply_linkback.py: apply the site-page line from make_linkback_wikitext.py to
files already on Commons, one careful edit at a time (Jordan approved this,
2026-11-23). Uses the uploader credentials in .env via upload_next.py's login.

Per file it:
  - re-reads the live page and skips it if the link is already there or the
    exported `find` text is no longer present exactly once (page changed)
  - inserts only the site-page line (no other change) and saves with the
    page's base timestamp, so a concurrent edit makes the save fail instead of
    overwriting it; nocreate, maxlag=5
  - logs every result to linkback/applied.jsonl and skips titles already done
  - waits DELAY seconds between edits and stops after 3 failures in a row

Usage:
  python3 apply_linkback.py --title "File:..."   # one file (the test)
  python3 apply_linkback.py --limit 20           # next 20
  python3 apply_linkback.py                      # all remaining
"""

import argparse
import glob
import json
import time
from pathlib import Path

from upload_next import COMMONS_API, load_env, make_session
import os

REPO = Path(__file__).parent
LOG = REPO / "linkback" / "applied.jsonl"
DELAY = 10
SUMMARY = "Add link to the file's Public Lands Institute site page"


def done_titles():
    if not LOG.exists():
        return set()
    return {json.loads(l)["title"] for l in LOG.read_text().splitlines()
            if l.strip() and json.loads(l)["result"] in ("edited", "already present")}


def log(rec):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{rec['result']:16} {rec['title']}")


def live(session, title):
    d = session.get(COMMONS_API, params={
        "action": "query", "prop": "revisions", "rvprop": "content|timestamp",
        "rvslots": "main", "titles": title, "format": "json", "formatversion": "2"}).json()
    p = d["query"]["pages"][0]
    if p.get("missing"):
        return None, None
    rev = p["revisions"][0]
    return rev["slots"]["main"]["content"], rev["timestamp"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    export = sorted(glob.glob(str(REPO / "linkback" / "commons_linkback_*.json")))[-1]
    records = [r for r in json.load(open(export)) if r.get("find") and r.get("replace")]
    if a.title:
        records = [r for r in records if r["title"] == a.title]
    skip = done_titles()
    records = [r for r in records if r["title"] not in skip]
    if a.limit:
        records = records[:a.limit]
    print(f"{len(records)} files to do ({len(skip)} already done) from {Path(export).name}")
    if not records:
        return

    load_env()
    user = os.environ["COMMONS_USERNAME"]
    bot = os.environ.get("COMMONS_BOT_NAME")
    session, csrf = make_session(f"{user}@{bot}" if bot else user, os.environ["COMMONS_BOT_PASSWORD"])

    fails = 0
    for i, r in enumerate(records):
        title, url = r["title"], f"https://publiclandsinstitute.net/sites/{r['slug']}.html"
        text, ts = live(session, title)
        base = {"title": title, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if text is None:
            log({**base, "result": "skipped", "why": "page missing"})
            continue
        if url in text:
            log({**base, "result": "already present"})
            continue
        if text.count(r["find"]) != 1:
            log({**base, "result": "skipped", "why": "page changed since export"})
            continue
        new = text.replace(r["find"], r["replace"], 1)
        res = session.post(COMMONS_API, data={
            "action": "edit", "title": title, "text": new, "summary": SUMMARY,
            "basetimestamp": ts, "nocreate": 1, "maxlag": 5,
            "token": csrf, "format": "json"}).json()
        if res.get("edit", {}).get("result") == "Success":
            log({**base, "result": "edited", "newrevid": res["edit"].get("newrevid")})
            fails = 0
        else:
            err = res.get("error", {})
            log({**base, "result": "FAILED", "why": f"{err.get('code')}: {err.get('info')}"})
            fails += 1
            if err.get("code") == "maxlag":
                time.sleep(60)
            if fails >= 3:
                print("3 failures in a row: stopping")
                return
        if i < len(records) - 1:
            time.sleep(DELAY)


if __name__ == "__main__":
    main()
