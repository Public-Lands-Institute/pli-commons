#!/usr/bin/env python3
"""
rollback_tags.py

Removes the incorrectly applied cleanup tags from ALL Zotero items
(including attachments and notes that should never have been tagged).

Removes: needs-year, possible-duplicate, needs-review

After this runs, re-run cleanup_zotero.py (without --dry-run) to correctly
apply those tags to top-level bibliography entries only.

Usage:
    python3 rollback_tags.py \
        --library-id 2767253 \
        --library-type group \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt
"""

import argparse
import sys
import time
from pyzotero import zotero

TAGS_TO_REMOVE = {"needs-year", "possible-duplicate", "needs-review"}
SLEEP = 0.5


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--library-id",   required=True)
    p.add_argument("--api-key",      required=True)
    p.add_argument("--library-type", default="group", choices=["user", "group"])
    return p.parse_args()


def main():
    args = parse_args()

    print(f"Connecting to Zotero …")
    zot = zotero.Zotero(args.library_id, args.library_type, args.api_key)
    try:
        zot.count_items()
        print("  Connection OK")
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Fetching ALL items (including attachments/notes) …")
    all_items = zot.everything(zot.items())
    print(f"  Fetched {len(all_items)} items")

    removed = 0
    skipped = 0

    for item in all_items:
        data = item["data"]
        tags = data.get("tags", [])
        existing = {t["tag"] for t in tags}
        bad_tags = existing & TAGS_TO_REMOVE

        if not bad_tags:
            skipped += 1
            continue

        data["tags"] = [t for t in tags if t["tag"] not in TAGS_TO_REMOVE]
        try:
            zot.update_item(item)
            removed += 1
            print(f"  [{item['key']}] removed: {', '.join(bad_tags)}")
            time.sleep(SLEEP)
        except Exception as exc:
            print(f"  ERROR [{item['key']}]: {exc}")

    print(f"\n── Done ─────────────────────────────────────────────────────")
    print(f"  Items cleaned: {removed}")
    print(f"  Already clean: {skipped}")
    print(f"\nNow re-run cleanup_zotero.py without --dry-run to correctly")
    print(f"apply tags to top-level bibliography entries only.")
    print(f"─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
