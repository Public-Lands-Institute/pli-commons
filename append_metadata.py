#!/usr/bin/env python3
"""
append_metadata.py

Appends {{Location}} templates and categories to PLI file pages on
Wikimedia Commons WITHOUT modifying existing descriptions.

This script ONLY:
  1. Adds {{Location}} template if not already present
  2. Adds new categories that don't already exist on the page

It does NOT touch the |description= field at all.

Usage:
    python3 append_metadata.py --dry-run    # preview, no edits
    python3 append_metadata.py              # apply live

Credentials via environment variables:
    export WIKI_USER=Publiclandsinstitute@wikiedits
    export WIKI_PASS=your-bot-password
"""

import json
import os
import re
import sys
import time
import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
DESCRIPTIONS_FILE = "descriptions.json"
EDIT_SUMMARY = "Adding GPS coordinates and categories (Public Lands Institute)"


def login(session, username, password):
    r = session.get(API_URL, params={
        "action": "query", "meta": "tokens", "type": "login", "format": "json"
    })
    token = r.json()["query"]["tokens"]["logintoken"]
    r = session.post(API_URL, data={
        "action": "login", "lgname": username, "lgpassword": password,
        "lgtoken": token, "format": "json"
    })
    result = r.json()["login"]["result"]
    if result != "Success":
        print(f"Login failed: {result}")
        sys.exit(1)
    print(f"Logged in as {username}")


def get_csrf_token(session):
    r = session.get(API_URL, params={
        "action": "query", "meta": "tokens", "format": "json"
    })
    return r.json()["query"]["tokens"]["csrftoken"]


def get_page_wikitext(session, title):
    r = session.get(API_URL, params={
        "action": "query", "titles": title, "prop": "revisions",
        "rvprop": "content", "rvslots": "main", "format": "json"
    })
    pages = r.json()["query"]["pages"]
    for page_id, page_data in pages.items():
        if page_id == "-1":
            return None
        return page_data["revisions"][0]["slots"]["main"]["*"]
    return None


def append_metadata(current_wikitext, entry):
    """
    ONLY adds Location template and categories.
    Does NOT modify the description field.
    """
    new_text = current_wikitext

    # --- Add {{Location}} template if not present ---
    location_template = '{{Location|' + entry["lat"] + '|' + entry["lon"] + '}}'
    location_pattern = r'\{\{Location\|[^}]*\}\}'

    if not re.search(location_pattern, new_text):
        # Insert after the license template
        license_pattern = r'(\{\{[Cc][Cc]-zero\}\}|\{\{CC0\}\}|\{\{PD-self\}\})'
        license_match = re.search(license_pattern, new_text)
        if license_match:
            insert_pos = license_match.end()
            new_text = new_text[:insert_pos] + '\n' + location_template + new_text[insert_pos:]
        else:
            # Insert before categories
            cat_match = re.search(r'\[\[Category:', new_text)
            if cat_match:
                new_text = new_text[:cat_match.start()] + location_template + '\n\n' + new_text[cat_match.start():]
            else:
                new_text = new_text.rstrip() + '\n\n' + location_template + '\n'

    # --- Add categories that don't already exist ---
    existing_cats = set(re.findall(r'\[\[Category:([^\]|]+)', new_text))
    new_cats = [c for c in entry["categories"] if c not in existing_cats]

    if new_cats:
        cat_text = '\n'.join(['[[Category:' + c + ']]' for c in new_cats])
        last_cat = list(re.finditer(r'\[\[Category:[^\]]+\]\]', new_text))
        if last_cat:
            insert_pos = last_cat[-1].end()
            new_text = new_text[:insert_pos] + '\n' + cat_text + new_text[insert_pos:]
        else:
            new_text = new_text.rstrip() + '\n\n' + cat_text + '\n'

    return new_text


def main():
    dry_run = "--dry-run" in sys.argv

    username = os.environ.get("WIKI_USER")
    password = os.environ.get("WIKI_PASS")

    if not username or not password:
        print("ERROR: Set WIKI_USER and WIKI_PASS environment variables.")
        sys.exit(1)

    with open(DESCRIPTIONS_FILE, "r") as f:
        entries = json.load(f)

    print(f"Loaded {len(entries)} files")
    if dry_run:
        print("=== DRY RUN MODE (no edits) ===\n")

    session = requests.Session()
    session.headers.update({"User-Agent": "PLICommonsBot/1.0 (publiclandsinstitute.net)"})

    if not dry_run:
        login(session, username, password)
        csrf_token = get_csrf_token(session)
    else:
        csrf_token = None

    success = 0
    errors = 0

    for i, entry in enumerate(entries):
        title = "File:" + entry["filename"]
        print(f"\n[{i+1}/{len(entries)}] Processing: {entry['filename']}")

        current = get_page_wikitext(session, title)
        if current is None:
            print(f"  WARNING: Page not found")
            errors += 1
            continue

        updated = append_metadata(current, entry)

        if current == updated:
            print(f"  No changes needed (Location and categories already present)")
            success += 1
            continue

        # Show what's being added
        has_location = bool(re.search(r'\{\{Location\|', current))
        existing_cats = set(re.findall(r'\[\[Category:([^\]|]+)', current))
        new_cats = [c for c in entry["categories"] if c not in existing_cats]
        if not has_location:
            print(f"  Adding: {{{{Location|{entry['lat']}|{entry['lon']}}}}}")
        if new_cats:
            print(f"  Adding categories: {new_cats}")

        if dry_run:
            print(f"  [DRY RUN] Would save")
            success += 1
        else:
            r = session.post(API_URL, data={
                "action": "edit", "title": title, "text": updated,
                "summary": EDIT_SUMMARY, "token": csrf_token,
                "format": "json", "bot": True
            })
            result = r.json()
            if "edit" in result and result["edit"]["result"] == "Success":
                print(f"  Saved")
                success += 1
            else:
                print(f"  ERROR: {json.dumps(result, indent=2)}")
                errors += 1
            time.sleep(2)

    print(f"\n{'='*50}")
    print(f"Done. {success} updated, {errors} errors out of {len(entries)} files.")


if __name__ == "__main__":
    main()
