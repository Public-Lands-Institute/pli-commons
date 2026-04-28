#!/usr/bin/env python3
"""
cleanup_zotero.py

Comprehensive data cleanup for the SOA Research Database Zotero group library.

What it does:
  1. Clears '[Abstract not available]' placeholders
  2. Clears junk abstracts (table-of-contents content pasted as abstracts)
  3. Normalizes tag capitalization (merges case-duplicate tags)
  4. Flags entries with missing Publication Year with tag 'needs-year'
  5. Flags exact duplicate titles with tag 'possible-duplicate'
  6. Flags likely out-of-scope entries with tag 'needs-review'

Usage:
    python3 cleanup_zotero.py \
        --library-id 2767253 \
        --library-type group \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt \
        --dry-run        # remove when ready to commit
"""

import argparse
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
from pyzotero import zotero

SCRIPT_DIR = Path(__file__).parent
NEEDS_REGEN_FILE = SCRIPT_DIR / "needs_regeneration.json"

SLEEP = 0.5   # seconds between write calls

# ── Out-of-scope detection ────────────────────────────────────────────────────
# Item types that are rarely academic reading list material
SUSPECT_TYPES = {"computerProgram", "film", "podcast", "radioBroadcast",
                 "tvBroadcast", "videoRecording"}

# Title keywords that suggest non-academic / out-of-scope content
SUSPECT_TITLE_PATTERNS = [
    r"\bsocial media\b.*\bbest practices\b",
    r"\bfor dummies\b",
    r"\bcomplete guide\b",
    r"\bhow to\b.*\b(make|build|grow|monetize)\b",
]

# Known fiction authors / titles (add more as needed)
KNOWN_FICTION_KEYS = set()   # populated by fuzzy abstract check below


def is_suspect(item_type: str, title: str, abstract: str) -> bool:
    t = (title or "").lower()
    a = (abstract or "").lower()
    if item_type in SUSPECT_TYPES:
        return True
    for pat in SUSPECT_TITLE_PATTERNS:
        if re.search(pat, t, re.I):
            return True
    # Abstract looks like a plot summary rather than scholarly description
    fiction_signals = ["novel", "fiction", "short story", "narrator", "protagonist",
                       "chapter 1", "chapter one"]
    if sum(1 for s in fiction_signals if s in a) >= 2:
        return True
    return False


# ── Junk abstract detection ───────────────────────────────────────────────────

def is_junk_abstract(text: str) -> bool:
    """Return True if the abstract looks like a ToC or other non-abstract content."""
    if not text or text.strip() == "[Abstract not available]":
        return False
    t = text.strip()
    # Many ' -- ' separators → table of contents
    if t.count(" -- ") > 3:
        return True
    # Starts with 'Chapter' or numbered section
    if re.match(r"^(Chapter \d|Introduction :|Part \d)", t):
        return True
    # Only a subtitle / heading with no sentence structure
    if len(t) < 60 and not re.search(r"[.!?]", t):
        return True
    return False


# ── Tag normalization ─────────────────────────────────────────────────────────

def preferred_form(variants: list[str]) -> str:
    """Pick the best capitalization: prefer Title Case, then most common."""
    title_cased = [v for v in variants if v == v.title()]
    if title_cased:
        return title_cased[0]
    lower = [v for v in variants if v == v.lower()]
    if lower:
        return lower[0]
    return variants[0]


def build_tag_normalization_map(all_items: list) -> dict[str, str]:
    """Return mapping old_tag → preferred_tag for case duplicates."""
    tag_set: set[str] = set()
    for item in all_items:
        for t in item["data"].get("tags", []):
            tag_set.add(t["tag"])

    lower_map: dict[str, list[str]] = {}
    for tag in tag_set:
        lower_map.setdefault(tag.lower(), []).append(tag)

    norm: dict[str, str] = {}
    for variants in lower_map.values():
        if len(variants) > 1:
            best = preferred_form(variants)
            for v in variants:
                if v != best:
                    norm[v] = best
    return norm


# ── Duplicate detection ───────────────────────────────────────────────────────

def find_duplicate_keys(all_items: list) -> set[str]:
    """Return set of Zotero keys that appear to be exact title duplicates."""
    seen: dict[str, str] = {}   # normalized_title → first key
    dupes: set[str] = set()
    for item in all_items:
        title = item["data"].get("title", "").strip().lower()
        if not title:
            continue
        if title in seen:
            dupes.add(item["key"])
            dupes.add(seen[title])
        else:
            seen[title] = item["key"]
    return dupes


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--library-id",   required=True)
    p.add_argument("--api-key",      required=True)
    p.add_argument("--library-type", default="group", choices=["user", "group"])
    p.add_argument("--dry-run",      action="store_true")
    return p.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print(f"Connecting to Zotero (id={args.library_id}, type={args.library_type}) …")
    zot = zotero.Zotero(args.library_id, args.library_type, args.api_key)
    try:
        zot.count_items()
        print("  Connection OK")
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Fetching all top-level items …")
    all_items = zot.everything(zot.top())
    print(f"  Fetched {len(all_items)} items")

    if args.dry_run:
        print("\n  DRY RUN — no changes will be written\n")

    # Pre-compute helpers
    tag_norm_map = build_tag_normalization_map(all_items)
    duplicate_keys = find_duplicate_keys(all_items)

    print(f"\n  Case-duplicate tag pairs to fix: {len(tag_norm_map)}")
    for old, new in tag_norm_map.items():
        print(f"    '{old}' → '{new}'")
    print(f"  Duplicate title keys: {len(duplicate_keys)}")
    print()

    # Counters
    cleared_keys        = []   # keys whose abstracts were cleared → need regen
    cleared_placeholder = 0
    cleared_junk        = 0
    tags_normalized     = 0
    flagged_no_year     = 0
    flagged_duplicate   = 0
    flagged_oos         = 0
    total_updated       = 0
    errors              = 0

    for item in all_items:
        data    = item["data"]
        key     = item["key"]
        title   = data.get("title", "")
        abstract = data.get("abstractNote", "").strip()
        item_type = data.get("itemType", "")
        year    = str(data.get("date", "") or "").strip()
        tags    = data.get("tags", [])
        tag_set = {t["tag"] for t in tags}

        changed  = False
        changes  = []

        # 1. Clear placeholder abstracts
        if abstract == "[Abstract not available]":
            data["abstractNote"] = ""
            changes.append("  cleared placeholder abstract")
            cleared_placeholder += 1
            cleared_keys.append(key)
            changed = True

        # 2. Clear junk/ToC abstracts
        elif is_junk_abstract(abstract):
            data["abstractNote"] = ""
            changes.append(f"  cleared junk abstract: {abstract[:60]!r}")
            cleared_junk += 1
            cleared_keys.append(key)
            changed = True

        # 3. Normalize tags
        new_tags = []
        tag_changed = False
        for t in tags:
            old_tag = t["tag"]
            new_tag = tag_norm_map.get(old_tag, old_tag)
            new_tags.append({"tag": new_tag})
            if new_tag != old_tag:
                tag_changed = True
        if tag_changed:
            # Deduplicate after normalization
            seen_tags: set[str] = set()
            deduped = []
            for t in new_tags:
                if t["tag"] not in seen_tags:
                    seen_tags.add(t["tag"])
                    deduped.append(t)
            data["tags"] = deduped
            tag_set = seen_tags
            changes.append(f"  normalized tags")
            tags_normalized += 1
            changed = True

        # 4. Flag missing year
        if not year and "needs-year" not in tag_set:
            data["tags"] = data.get("tags", []) + [{"tag": "needs-year"}]
            tag_set.add("needs-year")
            changes.append("  + tag: needs-year")
            flagged_no_year += 1
            changed = True

        # 5. Flag duplicates
        if key in duplicate_keys and "possible-duplicate" not in tag_set:
            data["tags"] = data.get("tags", []) + [{"tag": "possible-duplicate"}]
            tag_set.add("possible-duplicate")
            changes.append("  + tag: possible-duplicate")
            flagged_duplicate += 1
            changed = True

        # 6. Flag out-of-scope
        if is_suspect(item_type, title, abstract) and "needs-review" not in tag_set:
            data["tags"] = data.get("tags", []) + [{"tag": "needs-review"}]
            tag_set.add("needs-review")
            changes.append("  + tag: needs-review")
            flagged_oos += 1
            changed = True

        # Write
        if changed:
            short_title = title[:60]
            print(f"[{key}] {short_title}")
            for c in changes:
                print(c)

            if not args.dry_run:
                try:
                    zot.update_item(item)
                    total_updated += 1
                    time.sleep(SLEEP)
                except Exception as exc:
                    print(f"  ERROR: {exc}")
                    errors += 1
            else:
                total_updated += 1

    # Summary
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Placeholder abstracts cleared:  {cleared_placeholder}")
    print(f"  Junk/ToC abstracts cleared:     {cleared_junk}")
    print(f"  Tag case groups normalized:     {tags_normalized}")
    print(f"  Flagged missing year:           {flagged_no_year}")
    print(f"  Flagged possible duplicates:    {flagged_duplicate}")
    print(f"  Flagged needs-review:           {flagged_oos}")
    print(f"  Total items updated:            {total_updated}")
    if errors:
        print(f"  Errors:                         {errors}")
    if args.dry_run:
        print("\n  DRY RUN — rerun without --dry-run to commit.")
    elif cleared_keys:
        with open(NEEDS_REGEN_FILE, "w") as f:
            json.dump({
                "library_id":   args.library_id,
                "library_type": args.library_type,
                "keys":         cleared_keys,
            }, f, indent=2)
        print(f"\n  Saved {len(cleared_keys)} keys to {NEEDS_REGEN_FILE}")
        print("  Run regenerate_abstracts.py next to fill them in.")
    print("─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
