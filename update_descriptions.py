#!/usr/bin/env python3
"""
update_descriptions.py

Batch-update PLI file pages on Wikimedia Commons.
Replaces the thin "Public Lands Institute CC0 photograph" descriptions
with full geological/ecological descriptions, adds {{Location}} templates,
and appends categories without deleting existing page content.

Usage:
    python update_descriptions.py --dry-run          # preview changes, no edits
    python update_descriptions.py                     # apply changes live

Requires:
    pip install requests

Credentials:
    Set environment variables before running:
        export WIKI_USER="Publiclandsinstitute"
        export WIKI_PASS="your-bot-password-here"

    To get a bot password: Commons > Special:BotPasswords > create one with
    "Edit existing pages" and "High-volume editing" permissions.
"""

import json
import os
import re
import sys
import time
import requests

API_URL = "https://commons.wikimedia.org/w/api.php"
DESCRIPTIONS_FILE = "descriptions.json"
EDIT_SUMMARY = "Updating file description with geological metadata, GPS coordinates, and categories (Public Lands Institute)"


def login(session, username, password):
    """Log in to Commons via the MediaWiki API."""
    # Step 1: get login token
    r = session.get(API_URL, params={
        "action": "query",
        "meta": "tokens",
        "type": "login",
        "format": "json"
    })
    token = r.json()["query"]["tokens"]["logintoken"]

    # Step 2: log in
    r = session.post(API_URL, data={
        "action": "login",
        "lgname": username,
        "lgpassword": password,
        "lgtoken": token,
        "format": "json"
    })
    result = r.json()["login"]["result"]
    if result != "Success":
        print(f"Login failed: {result}")
        sys.exit(1)
    print(f"Logged in as {username}")


def get_csrf_token(session):
    """Get a CSRF token for editing."""
    r = session.get(API_URL, params={
        "action": "query",
        "meta": "tokens",
        "format": "json"
    })
    return r.json()["query"]["tokens"]["csrftoken"]


def get_page_wikitext(session, title):
    """Fetch the current wikitext of a page."""
    r = session.get(API_URL, params={
        "action": "query",
        "titles": title,
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json"
    })
    pages = r.json()["query"]["pages"]
    for page_id, page_data in pages.items():
        if page_id == "-1":
            return None
        return page_data["revisions"][0]["slots"]["main"]["*"]
    return None


def build_updated_wikitext(current_wikitext, entry):
    """
    Update the wikitext of a file page:
    1. Replace the English description with the new one
    2. Add/update {{Location}} template
    3. Add new categories (without duplicating existing ones)
    """
    new_text = current_wikitext

    # --- Update description ---
    # Match existing {{en|1=...}} description block
    en_desc_pattern = r'\{\{en\|1=.*?\}\}'
    new_en_desc = '{{en|1=' + entry["description"] + '}}'

    if re.search(en_desc_pattern, new_text, re.DOTALL):
        # Replace existing English description
        new_text = re.sub(en_desc_pattern, new_en_desc, new_text, count=1, flags=re.DOTALL)
    else:
        # No {{en|1=...}} found. Try to replace plain description text
        # Look for the description field in the {{Information}} template
        desc_pattern = r'(\|description\s*=\s*)(.*?)(\n\|)'
        match = re.search(desc_pattern, new_text, re.DOTALL | re.IGNORECASE)
        if match:
            new_text = re.sub(
                desc_pattern,
                r'\1' + new_en_desc + r'\3',
                new_text,
                count=1,
                flags=re.DOTALL | re.IGNORECASE
            )
        else:
            # Fallback: try to replace the simple description line
            simple_desc = r'Public Lands Institute CC0 photograph[^\n]*'
            if re.search(simple_desc, new_text):
                new_text = re.sub(simple_desc, new_en_desc, new_text, count=1)

    # --- Add/update {{Location}} template ---
    location_template = '{{Location|' + entry["lat"] + '|' + entry["lon"] + '}}'
    location_pattern = r'\{\{Location\|[^}]*\}\}'

    if re.search(location_pattern, new_text):
        # Update existing Location template
        new_text = re.sub(location_pattern, location_template, new_text, count=1)
    else:
        # Add Location template after the {{Information}} block or at the end
        info_end = re.search(r'(\}\}\s*\n)', new_text)
        if info_end:
            # Insert after the first closing }} (likely end of Information or license template)
            # Find a good insertion point - after license templates
            # Look for the CC0 or license line
            license_pattern = r'(\{\{cc-zero\}\}|\{\{CC0\}\}|\{\{PD-self\}\})'
            license_match = re.search(license_pattern, new_text, re.IGNORECASE)
            if license_match:
                insert_pos = license_match.end()
                new_text = new_text[:insert_pos] + '\n' + location_template + new_text[insert_pos:]
            else:
                # Just add before categories
                cat_match = re.search(r'\[\[Category:', new_text)
                if cat_match:
                    new_text = new_text[:cat_match.start()] + location_template + '\n\n' + new_text[cat_match.start():]
                else:
                    new_text = new_text.rstrip() + '\n\n' + location_template + '\n'

    # --- Add categories ---
    existing_cats = set(re.findall(r'\[\[Category:([^\]|]+)', new_text))
    new_cats = []
    for cat in entry["categories"]:
        if cat not in existing_cats:
            new_cats.append(cat)

    if new_cats:
        cat_text = '\n'.join(['[[Category:' + c + ']]' for c in new_cats])
        # Append categories at the end
        if re.search(r'\[\[Category:', new_text):
            # Add after last existing category
            last_cat = list(re.finditer(r'\[\[Category:[^\]]+\]\]', new_text))
            if last_cat:
                insert_pos = last_cat[-1].end()
                new_text = new_text[:insert_pos] + '\n' + cat_text + new_text[insert_pos:]
        else:
            new_text = new_text.rstrip() + '\n\n' + cat_text + '\n'

    return new_text


def edit_page(session, csrf_token, title, new_text, summary, dry_run=False):
    """Save updated wikitext to a page."""
    if dry_run:
        print(f"  [DRY RUN] Would save {title}")
        return True

    r = session.post(API_URL, data={
        "action": "edit",
        "title": title,
        "text": new_text,
        "summary": summary,
        "token": csrf_token,
        "format": "json",
        "bot": True
    })

    result = r.json()
    if "edit" in result and result["edit"]["result"] == "Success":
        print(f"  Saved: {title}")
        return True
    else:
        print(f"  ERROR saving {title}: {json.dumps(result, indent=2)}")
        return False


def main():
    dry_run = "--dry-run" in sys.argv

    # Load credentials
    username = os.environ.get("WIKI_USER", "Publiclandsinstitute")
    password = os.environ.get("WIKI_PASS")

    if not password:
        print("ERROR: Set WIKI_PASS environment variable to your bot password.")
        print("  Get one at: https://commons.wikimedia.org/wiki/Special:BotPasswords")
        print("  Grant permissions: Edit existing pages, High-volume editing")
        sys.exit(1)

    # Load descriptions
    with open(DESCRIPTIONS_FILE, "r") as f:
        entries = json.load(f)

    print(f"Loaded {len(entries)} file descriptions")
    if dry_run:
        print("=== DRY RUN MODE (no edits will be made) ===\n")

    # Start session
    session = requests.Session()
    session.headers.update({"User-Agent": "PLICommonsBot/1.0 (publiclandsinstitute.net)"})

    if not dry_run:
        login(session, username, password)
        csrf_token = get_csrf_token(session)
    else:
        csrf_token = None

    # Process each file
    success = 0
    errors = 0

    for i, entry in enumerate(entries):
        title = "File:" + entry["filename"]
        print(f"\n[{i+1}/{len(entries)}] Processing: {entry['filename']}")

        # Fetch current page
        current = get_page_wikitext(session, title)
        if current is None:
            print(f"  WARNING: Page not found: {title}")
            errors += 1
            continue

        # Build updated wikitext
        updated = build_updated_wikitext(current, entry)

        if current == updated:
            print(f"  No changes needed")
            success += 1
            continue

        if dry_run:
            # Show diff preview
            print(f"  Description: {entry['description'][:80]}...")
            print(f"  Location: {entry['lat']}, {entry['lon']}")
            existing_cats = set(re.findall(r'\[\[Category:([^\]|]+)', current))
            new_cats = [c for c in entry['categories'] if c not in existing_cats]
            print(f"  New categories: {new_cats}")
            edit_page(session, csrf_token, title, updated, EDIT_SUMMARY, dry_run=True)
            success += 1
        else:
            if edit_page(session, csrf_token, title, updated, EDIT_SUMMARY):
                success += 1
            else:
                errors += 1
            # Rate limit: 1 edit per 2 seconds to be polite
            time.sleep(2)

    print(f"\n{'='*50}")
    print(f"Done. {success} updated, {errors} errors out of {len(entries)} files.")
    if dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
