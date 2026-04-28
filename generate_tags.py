#!/usr/bin/env python3
"""
generate_tags.py

Uses Claude to suggest tags for Zotero items that have no subject tags.
Pulls the existing tag taxonomy from the library so suggestions stay
consistent with what's already there. Saves a checkpoint and pushes
results directly to Zotero.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 generate_tags.py \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt \
        --dry-run        # remove when ready to commit
"""

import argparse
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import anthropic
from pyzotero import zotero

LIBRARY_ID   = "2767253"
LIBRARY_TYPE = "group"
SCRIPT_DIR   = Path(__file__).parent
CHECKPOINT   = SCRIPT_DIR / "tag_checkpoint.json"

MODEL          = "claude-sonnet-4-6"
MAX_TOKENS     = 150
SLEEP_CLAUDE   = 1.5
SLEEP_ZOTERO   = 0.4
PROGRESS_EVERY = 25

FLAG_TAGS = {"needs-year", "possible-duplicate", "needs-review"}

SYSTEM_PROMPT = """\
You are a cataloguer for a Fine Art BFA/MFA program reading list. \
Given a bibliographic entry, suggest 3–6 tags that best describe its \
subject matter. Choose ONLY from the provided tag list — do not invent \
new tags. Return tags as a comma-separated list on a single line, nothing else.\
"""


def build_prompt(data: dict, tag_list: list[str]) -> str:
    def safe(k):
        return str(data.get(k, "") or "").strip()

    creators = data.get("creators", [])
    authors = "; ".join(
        f"{c.get('lastName', '')}, {c.get('firstName', '')}".strip(", ")
        for c in creators[:3] if c.get("lastName") or c.get("firstName")
    ) or safe("author")

    publisher = safe("publisher") or safe("publicationTitle")
    abstract  = safe("abstractNote")[:400]

    return (
        f"Title: {safe('title')}\n"
        f"Author: {authors}\n"
        f"Year: {safe('date')}\n"
        f"Publisher/Journal: {publisher}\n"
        f"Item Type: {safe('itemType')}\n"
        f"Abstract: {abstract}\n\n"
        f"Tag list:\n{', '.join(tag_list)}"
    )


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {}


def save_checkpoint(data: dict) -> None:
    with open(CHECKPOINT, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--api-key", required=True, help="Zotero API key")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print("Connecting to Zotero …")
    zot = zotero.Zotero(LIBRARY_ID, LIBRARY_TYPE, args.api_key)
    try:
        zot.count_items()
        print("  Connection OK")
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n  DRY RUN — no changes will be written\n")

    # ── Build tag taxonomy from the library ──────────────────────────────────
    print("Fetching all items to build tag taxonomy …")
    all_items = zot.everything(zot.top())
    print(f"  {len(all_items)} items")

    tag_counts: dict[str, int] = {}
    for item in all_items:
        for t in item["data"].get("tags", []):
            tag = t["tag"]
            if tag not in FLAG_TAGS:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Use tags that appear at least twice — avoids one-off noise
    tag_list = sorted(k for k, v in tag_counts.items() if v >= 2)
    print(f"  Tag taxonomy: {len(tag_list)} tags (appearing ≥2 times)")

    # ── Find items with no subject tags ──────────────────────────────────────
    untagged = []
    for item in all_items:
        subject_tags = [
            t["tag"] for t in item["data"].get("tags", [])
            if t["tag"] not in FLAG_TAGS
        ]
        if not subject_tags:
            untagged.append(item)

    print(f"  Items needing tags: {len(untagged)}")

    checkpoint = load_checkpoint()
    already_done = set(checkpoint.keys())
    remaining = [i for i in untagged if i["key"] not in already_done]
    print(f"  Checkpoint: {len(already_done)} done previously")
    print(f"  Remaining:  {len(remaining)}")
    print()

    client    = anthropic.Anthropic(api_key=anthropic_key)
    generated = 0
    failed    = 0
    start     = time.time()

    for idx, item in enumerate(remaining, 1):
        key   = item["key"]
        data  = item["data"]
        title = data.get("title", "")[:60]

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(data, tag_list)}],
            )
            raw = " ".join(
                b.text for b in response.content if b.type == "text"
            ).strip()

            # Parse comma-separated tags, validate against taxonomy
            tag_set = set(t.lower() for t in tag_list)
            suggested = []
            for t in raw.split(","):
                t = t.strip().strip(".")
                if t.lower() in tag_set:
                    # Use the canonical casing from tag_list
                    canonical = next((x for x in tag_list if x.lower() == t.lower()), t)
                    suggested.append(canonical)

            if suggested:
                checkpoint[key] = suggested
                save_checkpoint(checkpoint)
                generated += 1
                if args.dry_run or idx <= 5:
                    print(f"[{key}] {title}")
                    print(f"  tags: {', '.join(suggested)}")
            else:
                failed += 1

        except Exception as exc:
            print(f"  [ERROR] {key} {title!r}: {exc}")
            failed += 1

        if idx % PROGRESS_EVERY == 0 or idx == len(remaining):
            elapsed = time.time() - start
            rate = generated / elapsed if elapsed > 0 else 0
            left = (len(remaining) - idx) / rate if rate > 0 else 0
            print(
                f"  [{idx}/{len(remaining)}]  generated={generated}  failed={failed}  "
                f"ETA {str(timedelta(seconds=int(left)))}"
            )

        if idx < len(remaining):
            time.sleep(SLEEP_CLAUDE)

    print(f"\nGeneration done: {generated} tagged, {failed} failed.")

    if args.dry_run:
        print("\n  DRY RUN — rerun without --dry-run to push to Zotero.")
        return

    # ── Push tags to Zotero ──────────────────────────────────────────────────
    print("\nPushing tags to Zotero …")
    print("  Re-fetching items to get latest versions …")
    fresh_items = zot.everything(zot.top())
    item_map = {i["key"]: i for i in fresh_items}

    pushed  = 0
    p_errors = 0

    for key, new_tags in checkpoint.items():
        if key not in item_map:
            continue
        item = item_map[key]
        existing = {t["tag"] for t in item["data"].get("tags", [])}
        to_add = [t for t in new_tags if t not in existing]
        if not to_add:
            continue

        item["data"]["tags"] = item["data"].get("tags", []) + [{"tag": t} for t in to_add]
        try:
            zot.update_item(item)
            pushed += 1
            time.sleep(SLEEP_ZOTERO)
        except Exception as exc:
            print(f"  [ERROR] push key={key}: {exc}")
            p_errors += 1

    print(f"\n── Summary ──────────────────────────────────────────────────")
    print(f"  Items tagged by Claude:  {generated}")
    print(f"  Generation failures:     {failed}")
    print(f"  Pushed to Zotero:        {pushed}")
    if p_errors:
        print(f"  Push errors:             {p_errors}")
    print(f"─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
