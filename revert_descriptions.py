#!/usr/bin/env python3
"""
revert_descriptions.py

Reverts all PLI file pages on Wikimedia Commons to their previous revision,
undoing the batch description update.

Usage:
    python3 revert_descriptions.py --dry-run    # preview, no edits
    python3 revert_descriptions.py              # revert live

Credentials via environment variables:
    export WIKI_USER=Publiclandsinstitute@wikiedits
    export WIKI_PASS=your-bot-password
"""

import json
import os
import sys
import time
import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
DESCRIPTIONS_FILE = "descriptions.json"
EDIT_SUMMARY = "Reverting to previous version (restoring full description with Indigenous territories and species data)"


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


def get_previous_revision(session, title):
    """Get the content of the revision before the most recent one."""
    r = session.get(API_URL, params={
        "action": "query",
        "titles": title,
        "prop": "revisions",
        "rvprop": "content|ids|comment",
        "rvslots": "main",
        "rvlimit": 2,
        "format": "json"
    })
    pages = r.json()["query"]["pages"]
    for page_id, page_data in pages.items():
        if page_id == "-1":
            return None, None
        revisions = page_data.get("revisions", [])
        if len(revisions) < 2:
            print(f"  Only one revision exists, cannot revert")
            return None, None
        # revisions[0] is current, revisions[1] is previous
        current_comment = revisions[0].get("comment", "")
        prev_content = revisions[1]["slots"]["main"]["*"]
        prev_revid = revisions[1]["revid"]
        return prev_content, prev_revid
    return None, None


def main():
    dry_run = "--dry-run" in sys.argv

    username = os.environ.get("WIKI_USER")
    password = os.environ.get("WIKI_PASS")

    if not username or not password:
        print("ERROR: Set WIKI_USER and WIKI_PASS environment variables.")
        sys.exit(1)

    with open(DESCRIPTIONS_FILE, "r") as f:
        entries = json.load(f)

    print(f"Will revert {len(entries)} files")
    if dry_run:
        print("=== DRY RUN MODE ===\n")

    session = requests.Session()
    session.headers.update({"User-Agent": "PLICommonsBot/1.0 (publiclandsinstitute.net)"})

    if not dry_run:
        login(session, username, password)
        csrf_token = get_csrf_token(session)

    success = 0
    errors = 0

    for i, entry in enumerate(entries):
        title = "File:" + entry["filename"]
        print(f"\n[{i+1}/{len(entries)}] Reverting: {entry['filename']}")

        prev_content, prev_revid = get_previous_revision(session, title)

        if prev_content is None:
            print(f"  SKIP: Could not get previous revision")
            errors += 1
            continue

        if dry_run:
            print(f"  Would revert to revision {prev_revid}")
            print(f"  Previous description starts with: {prev_content[:100]}...")
            success += 1
        else:
            r = session.post(API_URL, data={
                "action": "edit",
                "title": title,
                "text": prev_content,
                "summary": EDIT_SUMMARY,
                "token": csrf_token,
                "format": "json",
                "bot": True
            })
            result = r.json()
            if "edit" in result and result["edit"]["result"] == "Success":
                print(f"  Reverted successfully")
                success += 1
            else:
                print(f"  ERROR: {json.dumps(result, indent=2)}")
                errors += 1
            time.sleep(2)

    print(f"\n{'='*50}")
    print(f"Done. {success} reverted, {errors} errors out of {len(entries)} files.")


if __name__ == "__main__":
    main()
