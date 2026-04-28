#!/usr/bin/env python3
"""
apply_tags_to_zotero.py

Pushes Generated_Tags and/or generated abstracts from SOA_Research_Final.csv
back to a Zotero library via the Zotero Web API.

Usage:
    pip install pyzotero pandas
    python apply_tags_to_zotero.py \
        --library-id 7446838 \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt \
        --csv SOA_Research_Final.csv \
        --write-abstracts \
        --dry-run        # remove this flag when ready to commit

Options:
    --library-id    Zotero user/group library ID
    --api-key       Zotero API key
    --csv           Path to the merged CSV (default: SOA_Research_Final.csv)
    --write-abstracts  Also push generated abstracts to Zotero abstractNote
    --dry-run       Print what would change without writing anything
    --library-type  'user' (default) or 'group'
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
from pyzotero import zotero

SCRIPT_DIR = Path(__file__).parent
SLEEP = 0.5   # seconds between Zotero API write calls


def parse_args():
    p = argparse.ArgumentParser(description="Push tags/abstracts to Zotero")
    p.add_argument("--library-id",    required=True,  help="Zotero library ID")
    p.add_argument("--api-key",       required=True,  help="Zotero API key")
    p.add_argument("--csv",           default=str(SCRIPT_DIR / "SOA_Research_Final.csv"),
                   help="Path to merged CSV")
    p.add_argument("--write-abstracts", action="store_true",
                   help="Push generated abstracts to Zotero abstractNote")
    p.add_argument("--dry-run",       action="store_true",
                   help="Print changes without writing to Zotero")
    p.add_argument("--library-type",  default="user", choices=["user", "group"],
                   help="'user' or 'group' library (default: user)")
    return p.parse_args()


def safe(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def tags_from_string(tag_string: str) -> list[dict]:
    """Convert a semicolon-separated tag string to Zotero tag objects."""
    if not tag_string:
        return []
    return [{"tag": t.strip()} for t in tag_string.split(";") if t.strip()]


def main():
    args = parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path, dtype=str)
    key_col = df.columns[0]  # "Key"
    print(f"  Rows: {len(df)}")

    # Connect to Zotero
    print(f"\nConnecting to Zotero (library_id={args.library_id}, type={args.library_type}) …")
    zot = zotero.Zotero(args.library_id, args.library_type, args.api_key)

    # Quick connection test
    try:
        zot.count_items()
        print("  Connection OK")
    except Exception as exc:
        print(f"  ERROR: Could not connect to Zotero: {exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch all items from Zotero (paged automatically)
    print("\nFetching all items from Zotero …")
    all_items = zot.everything(zot.items())
    print(f"  Fetched {len(all_items)} items")

    # Build a lookup: Zotero key → item dict
    zot_by_key = {item["key"]: item for item in all_items}

    # ── Process each CSV row ─────────────────────────────────────────────────

    updated = 0
    skipped = 0
    not_found = 0
    errors = 0

    rows_to_process = df[
        df["Generated_Abstract"].notna() & (df["Generated_Abstract"].str.strip() != "")
        | df["Manual Tags"].notna()
    ]

    print(f"\nProcessing {len(rows_to_process)} rows …")
    if args.dry_run:
        print("  DRY RUN — no changes will be written\n")

    for _, row in df.iterrows():
        key = safe(row[key_col])
        if not key:
            continue

        if key not in zot_by_key:
            not_found += 1
            continue

        item = zot_by_key[key]
        data = item["data"]
        changed = False
        changes = []

        # ── Tags ──────────────────────────────────────────────────────────
        generated_tags = tags_from_string(safe(row.get("Generated_Tags", "")))
        manual_tags    = tags_from_string(safe(row.get("Manual Tags", "")))
        new_tags = generated_tags + manual_tags

        if new_tags:
            existing_tags = {t["tag"] for t in data.get("tags", [])}
            incoming_tags = {t["tag"] for t in new_tags}
            to_add = incoming_tags - existing_tags
            if to_add:
                merged_tags = data.get("tags", []) + [{"tag": t} for t in to_add]
                data["tags"] = merged_tags
                changes.append(f"  + {len(to_add)} tag(s): {', '.join(sorted(to_add))}")
                changed = True

        # ── Abstract ──────────────────────────────────────────────────────
        if args.write_abstracts:
            generated_abstract = safe(row.get("Generated_Abstract", ""))
            existing_abstract  = safe(data.get("abstractNote", ""))

            if generated_abstract and not existing_abstract:
                data["abstractNote"] = generated_abstract
                changes.append(f"  + abstract ({len(generated_abstract)} chars)")
                changed = True

        # ── Write ─────────────────────────────────────────────────────────
        if changed:
            title = safe(row.get("Title", ""))[:60]
            print(f"[{key}] {title}")
            for c in changes:
                print(c)

            if not args.dry_run:
                try:
                    zot.update_item(item)
                    updated += 1
                    time.sleep(SLEEP)
                except Exception as exc:
                    print(f"  ERROR writing to Zotero: {exc}")
                    errors += 1
            else:
                updated += 1
        else:
            skipped += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Items updated:   {updated}")
    print(f"  Already current: {skipped}")
    print(f"  Not in Zotero:   {not_found}")
    if errors:
        print(f"  Errors:          {errors}")
    if args.dry_run:
        print("\n  DRY RUN complete — rerun without --dry-run to commit changes.")
    print("─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
