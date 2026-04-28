#!/usr/bin/env python3
"""
fill_metadata.py

Looks up missing metadata (author, year, publisher) for Zotero entries
using CrossRef (journals/articles) and Open Library (books).
Only fills fields that are currently blank — never overwrites existing data.

Usage:
    python3 fill_metadata.py \
        --library-id 2767253 \
        --library-type group \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt \
        --dry-run        # remove when ready to commit
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
import urllib.request
import json
from pathlib import Path
from pyzotero import zotero

SLEEP_API   = 1.0   # between CrossRef/OpenLibrary calls
SLEEP_ZOTERO = 0.5  # between Zotero write calls
SCRIPT_DIR  = Path(__file__).parent
CACHE_FILE  = SCRIPT_DIR / "metadata_cache.json"

# CrossRef polite pool email
CROSSREF_EMAIL = "jordan.tate@gmail.com"


# ── HTTP helper ───────────────────────────────────────────────────────────────

def get_json(url: str, timeout: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"PLICommonsMetadata/1.0 (mailto:{CROSSREF_EMAIL})"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── CrossRef lookup ───────────────────────────────────────────────────────────

def crossref_lookup(title: str, author: str = "") -> dict | None:
    query = title
    if author:
        query += f" {author}"
    q = urllib.parse.quote(query)
    url = f"https://api.crossref.org/works?query={q}&rows=1&select=title,author,published,publisher,type"
    data = get_json(url)
    if not data:
        return None
    items = data.get("message", {}).get("items", [])
    if not items:
        return None
    item = items[0]

    # Verify it's a reasonable title match
    result_title = " ".join(item.get("title", [""])).lower()
    if not _titles_match(title.lower(), result_title):
        return None

    authors = []
    for a in item.get("author", []):
        last  = a.get("family", "")
        first = a.get("given", "")
        if last:
            authors.append(f"{last}, {first}".strip(", "))

    year = ""
    pub  = item.get("published", {})
    parts = pub.get("date-parts", [[]])
    if parts and parts[0]:
        year = str(parts[0][0])

    return {
        "authors":   authors,
        "year":      year,
        "publisher": item.get("publisher", ""),
    }


# ── Google Books lookup ───────────────────────────────────────────────────────

def googlebooks_lookup(title: str, author: str = "") -> dict | None:
    query = f'intitle:"{title}"'
    if author:
        query += f' inauthor:"{author}"'
    q = urllib.parse.quote(query)
    url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=1&fields=items(volumeInfo)"
    data = get_json(url)
    if not data:
        return None
    items = data.get("items", [])
    if not items:
        return None
    info = items[0].get("volumeInfo", {})

    result_title = info.get("title", "").lower()
    if not _titles_match(title.lower(), result_title):
        return None

    raw_authors = info.get("authors", [])
    authors = []
    for a in raw_authors[:3]:
        parts = a.strip().rsplit(" ", 1)
        if len(parts) == 2:
            authors.append(f"{parts[1]}, {parts[0]}")
        else:
            authors.append(a)

    year = ""
    published = info.get("publishedDate", "")
    if published:
        year = published[:4]

    publisher = info.get("publisher", "")

    return {
        "authors":   authors,
        "year":      year,
        "publisher": publisher,
    }


# ── Open Library lookup ───────────────────────────────────────────────────────

def openlibrary_lookup(title: str, author: str = "") -> dict | None:
    query = urllib.parse.quote(f"{title} {author}".strip())
    url   = f"https://openlibrary.org/search.json?q={query}&limit=1&fields=title,author_name,first_publish_year,publisher"
    data  = get_json(url)
    if not data:
        return None
    docs = data.get("docs", [])
    if not docs:
        return None
    doc = docs[0]

    result_title = doc.get("title", "").lower()
    if not _titles_match(title.lower(), result_title):
        return None

    raw_authors = doc.get("author_name", [])
    # Open Library gives "First Last" — convert to "Last, First"
    authors = []
    for a in raw_authors[:3]:
        parts = a.strip().rsplit(" ", 1)
        if len(parts) == 2:
            authors.append(f"{parts[1]}, {parts[0]}")
        else:
            authors.append(a)

    publishers = doc.get("publisher", [])
    publisher  = publishers[0] if publishers else ""

    return {
        "authors":   authors,
        "year":      str(doc.get("first_publish_year", "")),
        "publisher": publisher,
    }


def _titles_match(a: str, b: str) -> bool:
    """True if titles share enough words to be the same work."""
    a_words = set(a.split()) - {"the", "a", "an", "of", "in", "and", "or"}
    b_words = set(b.split()) - {"the", "a", "an", "of", "in", "and", "or"}
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / max(len(a_words), len(b_words))
    return overlap >= 0.6


# ── Zotero data helpers ───────────────────────────────────────────────────────

def get_existing_authors(data: dict) -> list[str]:
    return [
        f"{c.get('lastName','')}, {c.get('firstName','')}"
        for c in data.get("creators", [])
        if c.get("lastName") or c.get("firstName")
    ]


def authors_to_creators(authors: list[str], creator_type: str = "author") -> list[dict]:
    creators = []
    for a in authors:
        if "," in a:
            last, first = a.split(",", 1)
            creators.append({
                "creatorType": creator_type,
                "lastName":    last.strip(),
                "firstName":   first.strip(),
            })
        else:
            creators.append({"creatorType": creator_type, "name": a.strip()})
    return creators


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--library-id",   required=True)
    p.add_argument("--api-key",      required=True)
    p.add_argument("--library-type", default="group", choices=["user", "group"])
    p.add_argument("--dry-run",      action="store_true")
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

    print("Fetching top-level items …")
    all_items = zot.everything(zot.top())
    print(f"  Fetched {len(all_items)} items")

    if args.dry_run:
        print("\n  DRY RUN — no changes will be written\n")

    # Load cache to avoid re-hitting APIs on restart
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            cache = json.load(f)

    updated   = 0
    not_found = 0
    skipped   = 0

    # Only process items missing at least one of: author, year, publisher
    needs_work = []
    for item in all_items:
        data = item["data"]
        missing_author    = not get_existing_authors(data)
        missing_year      = not str(data.get("date", "") or "").strip()
        missing_publisher = not str(data.get("publisher", "") or "").strip()
        if missing_author or missing_year or missing_publisher:
            needs_work.append((item, missing_author, missing_year, missing_publisher))

    print(f"  Items missing metadata: {len(needs_work)}")
    print()

    for idx, (item, miss_author, miss_year, miss_pub) in enumerate(needs_work, 1):
        data  = item["data"]
        key   = item["key"]
        title = str(data.get("title", "") or "").strip()
        if not title:
            skipped += 1
            continue

        existing_authors = get_existing_authors(data)
        author_hint = existing_authors[0] if existing_authors else ""

        # Check cache first
        cache_key = f"{title.lower()}|{author_hint.lower()}"
        if cache_key in cache:
            result = cache[cache_key]
        else:
            item_type = data.get("itemType", "")
            # Try CrossRef first for articles, Open Library for books
            if item_type in ("journalArticle", "conferencePaper", "report"):
                result = (crossref_lookup(title, author_hint)
                          or openlibrary_lookup(title, author_hint)
                          or googlebooks_lookup(title, author_hint))
            else:
                result = (openlibrary_lookup(title, author_hint)
                          or googlebooks_lookup(title, author_hint)
                          or crossref_lookup(title, author_hint))

            cache[cache_key] = result or {}
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2)
            time.sleep(SLEEP_API)

        if not result:
            not_found += 1
            if idx % 25 == 0:
                print(f"  [{idx}/{len(needs_work)}] updated={updated} not_found={not_found}")
            continue

        changed = False
        changes = []

        # Fill missing author
        if miss_author and result.get("authors"):
            creator_type = "author"
            if data.get("itemType") == "bookSection":
                creator_type = "bookAuthor"
            new_creators = authors_to_creators(result["authors"], creator_type)
            existing = data.get("creators", [])
            data["creators"] = existing + new_creators
            changes.append(f"  + author: {', '.join(result['authors'][:2])}")
            changed = True

        # Fill missing year
        if miss_year and result.get("year"):
            data["date"] = result["year"]
            changes.append(f"  + year: {result['year']}")
            changed = True

        # Fill missing publisher
        if miss_pub and result.get("publisher"):
            field = "publisher" if data.get("itemType") != "journalArticle" else "publicationTitle"
            if not str(data.get(field, "") or "").strip():
                data[field] = result["publisher"]
                changes.append(f"  + {field}: {result['publisher']}")
                changed = True

        if changed:
            print(f"[{key}] {title[:60]}")
            for c in changes:
                print(c)
            if not args.dry_run:
                try:
                    zot.update_item(item)
                    updated += 1
                    time.sleep(SLEEP_ZOTERO)
                except Exception as exc:
                    print(f"  ERROR: {exc}")
            else:
                updated += 1
        else:
            skipped += 1

        if idx % 25 == 0:
            print(f"  [{idx}/{len(needs_work)}] updated={updated} not_found={not_found} skipped={skipped}")

    print(f"\n── Summary ──────────────────────────────────────────────────")
    print(f"  Metadata filled:  {updated}")
    print(f"  Not found in APIs:{not_found}")
    print(f"  Skipped:          {skipped}")
    if args.dry_run:
        print("\n  DRY RUN — rerun without --dry-run to commit.")
    print(f"─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
