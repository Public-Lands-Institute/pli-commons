#!/usr/bin/env python3
"""Tag Black Hills National Forest 011-018 on Commons for speedy deletion
({{Db-author}}). They are the Bighorn National Forest 001-008 frames
(_DSF2909 .. _DSF2940), re-uploaded 2026-09-11/12 under Black Hills because
the TIFFs had been copied into img/full/black-hills-national-forest/.
Then remove their rows from upload_log.json (backup kept).

Run:  python3 tag_black_hills_bighorn_dupes.py --dry-run
      python3 tag_black_hills_bighorn_dupes.py
"""
import os, sys, json, shutil
from upload_next import load_env, make_session, COMMONS_API

PAIRS = [(f"Public Lands Institute - Black Hills National Forest - {n + 10:03d}.tif",
          f"Public Lands Institute - Bighorn National Forest - {n:03d}.tif") for n in range(1, 9)]
LOG = "upload_log.json"


def main():
    dry = "--dry-run" in sys.argv
    for bh, bg in PAIRS:
        print(f"  {bh}  duplicates  {bg}")
    if dry:
        return
    load_env()
    user = os.environ.get("COMMONS_USERNAME")
    bot = os.environ.get("COMMONS_BOT_NAME")
    pw = os.environ.get("COMMONS_BOT_PASSWORD")
    if not user or not pw:
        sys.exit("ERROR: COMMONS_USERNAME and COMMONS_BOT_PASSWORD must be set in .env")
    session, token = make_session(f"{user}@{bot}" if bot else user, pw)
    for bh, bg in PAIRS:
        tag = ("{{Db-author|reason=Uploaded in error under the wrong site; same frame as "
               f"[[:File:{bg}]]}}}}\n")
        r = session.post(COMMONS_API, data={
            "action": "edit", "title": f"File:{bh}", "prependtext": tag, "nocreate": 1,
            "summary": f"Speedy deletion (author request): duplicate of [[:File:{bg}]] uploaded under the wrong site",
            "token": token, "format": "json"})
        res = r.json()
        print(bh, res.get("edit", {}).get("result") or res)

    shutil.copy(LOG, LOG + ".pre-bh-dupes.bak")
    log = json.load(open(LOG))
    drop = {bh for bh, _ in PAIRS}
    if isinstance(log, list):
        new = [e for e in log if e.get("commons_filename") not in drop]
    else:
        new = {k: v for k, v in log.items() if v.get("commons_filename") not in drop}
    json.dump(new, open(LOG, "w"), indent=2, ensure_ascii=False)
    print(f"upload_log.json: removed {len(log) - len(new)} rows (backup {LOG}.pre-bh-dupes.bak)")


if __name__ == "__main__":
    main()
