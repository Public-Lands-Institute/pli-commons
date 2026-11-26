#!/usr/bin/env python3
"""
make_linkback_wikitext.py: READ-ONLY. Writes the wikitext to add to every PLI
file already on Wikimedia Commons, for applying by hand or with a tool of your
choice. It never logs in and never edits Commons.

For each file it reads the current page text from the Commons API and works out:
  1. the site-page line (same text upload_next.py now writes for new uploads),
     inserted just before the closing }} of the English description, and
  2. [[Category:Public Lands Institute]] if the page does not already carry it.

Outputs (in linkback/):
  commons_linkback_<date>.md     human-readable, grouped by site
  commons_linkback_<date>.json   one record per file: title, find/replace for the
                                 site-page line, append for the category, notes
  Category_Public_Lands_Institute.wikitext
                                 proposed text for the category page (hidden)

Usage:
  python3 make_linkback_wikitext.py
"""

import datetime
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

REPO_DIR = Path(__file__).parent
PLI_DIR = Path.home() / "PLI"
SITES_JSON = PLI_DIR / "sites.json"
PHOTOS_JSON = PLI_DIR / "data" / "photos.json"
OUT_DIR = REPO_DIR / "linkback"
API = "https://commons.wikimedia.org/w/api.php"
UA = "PLI-linkback-readonly/1.0 (https://publiclandsinstitute.net)"
PREFIX = "Public Lands Institute - "
CATEGORY = "Public Lands Institute"
CAT_LINE = f"[[Category:{CATEGORY}]]"

sys.path.insert(0, str(REPO_DIR))
from upload_next import site_page_line  # noqa: E402  same line as new uploads


def api_get(session, params):
    params = {**params, "format": "json", "formatversion": "2"}
    for attempt in range(4):
        r = session.get(API, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        time.sleep(5 * (attempt + 1))
    r.raise_for_status()


def list_files(session):
    """Every File: page whose name starts with the PLI prefix."""
    titles, cont = [], {}
    while True:
        d = api_get(session, {"action": "query", "list": "allimages",
                              "aiprefix": PREFIX.replace(" ", "_"), "ailimit": "500", **cont})
        titles += ["File:" + i["name"].replace("_", " ") for i in d["query"]["allimages"]]
        if "continue" not in d:
            return titles
        cont = d["continue"]


def fetch_texts(session, titles):
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = api_get(session, {"action": "query", "prop": "revisions", "rvprop": "content|timestamp",
                              "rvslots": "main", "titles": "|".join(batch)})
        for p in d["query"]["pages"]:
            if p.get("missing"):
                continue
            rev = p["revisions"][0]
            out[p["title"]] = (rev["slots"]["main"]["content"], rev["timestamp"])
        time.sleep(1)
    return out


def fetch_page(session, title):
    d = api_get(session, {"action": "query", "prop": "revisions", "rvprop": "content",
                          "rvslots": "main", "titles": title})
    p = d["query"]["pages"][0]
    if p.get("missing"):
        return None
    return p["revisions"][0]["slots"]["main"]["content"]


def en_description_end(text):
    """Index of the closing }} of the {{en|1=...}} inside |description=,
    matching nested templates. None if the page does not have that shape."""
    m = re.search(r"\|\s*description\s*=\s*\{\{\s*en\s*\|\s*(1\s*=)?", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) - 1:
        two = text[i:i + 2]
        if two == "{{":
            depth += 1
            i += 2
        elif two == "}}":
            depth -= 1
            if depth == 0:
                return i
            i += 2
        else:
            i += 1
    return None


def main():
    sites = {s["slug"]: s for s in json.loads(SITES_JSON.read_text())}
    photos = json.loads(PHOTOS_JSON.read_text())
    file_slug = {}
    for slug, entries in photos.items():
        for e in entries:
            if e.get("f"):
                file_slug["File:" + e["f"]] = slug
    name_slug = {s["name"]: slug for slug, s in sites.items()}

    session = requests.Session()
    session.headers["User-Agent"] = UA
    titles = list_files(session)
    print(f"{len(titles)} PLI files on Commons")
    texts = fetch_texts(session, titles)

    records, by_site = [], defaultdict(list)
    for title in sorted(texts):
        text, ts = texts[title]
        slug = file_slug.get(title)
        if not slug:  # fall back to the site name in the filename
            m = re.match(r"File:Public Lands Institute - (.+) - \d+\.\w+$", title)
            slug = name_slug.get(m.group(1)) if m else None
            orphan = True
        else:
            orphan = False
        rec = {"title": title, "slug": slug, "revision_timestamp": ts,
               "add_site_line": None, "add_category": False, "find": None,
               "replace": None, "notes": []}
        if orphan and slug:
            rec["notes"].append("not in data/photos.json (pulled from the index); optional")
        if not slug or slug not in sites:
            rec["notes"].append("no matching site in sites.json; not handled")
            records.append(rec)
            by_site["(unmatched)"].append(rec)
            continue
        line = site_page_line(sites[slug])
        url = f"https://publiclandsinstitute.net/sites/{slug}.html"
        new = text
        if url in text:
            rec["notes"].append("site page link already present")
        else:
            end = en_description_end(text)
            if end is None:
                rec["notes"].append("no {{en|1=...}} description found; add the line by hand")
            else:
                rec["add_site_line"] = "<br />" + line
                new = text[:end] + "<br />" + line + text[end:]
        if not re.search(r"\[\[\s*Category\s*:\s*Public[ _]Lands[ _]Institute\s*(\|[^\]]*)?\]\]", text):
            rec["add_category"] = True
            new = new.rstrip("\n") + "\n" + CAT_LINE
        if rec["add_site_line"]:
            # Smallest unique find/replace pair ending at the description's }}
            end = en_description_end(text)
            n = 80
            while text.count(text[max(0, end - n):end + 2]) > 1 and n < end:
                n += 80
            rec["find"] = text[max(0, end - n):end + 2]
            rec["replace"] = text[max(0, end - n):end] + rec["add_site_line"] + "}}"
        rec["append"] = ("\n" + CAT_LINE) if rec["add_category"] else None
        check = text.replace(rec["find"], rec["replace"], 1) if rec["find"] else text
        if rec["append"]:
            check = check.rstrip("\n") + rec["append"]
        assert check == new, title
        records.append(rec)
        by_site[slug].append(rec)

    OUT_DIR.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    (OUT_DIR / f"commons_linkback_{today}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1) + "\n")

    todo = [r for r in records if r["add_site_line"] or r["add_category"]]
    md = [f"# Commons link-back wikitext, {today}",
          "",
          "Read-only export: nothing here has been applied to Commons.",
          f"{len(records)} PLI files found on Commons; {len(todo)} need an addition.",
          f"{sum(1 for r in records if r['add_site_line'])} need the site-page line; "
          f"{sum(1 for r in records if r['add_category'])} need [[Category:{CATEGORY}]].",
          "",
          "Where to put the site-page line: immediately before the closing `}}` of the",
          "`{{en|1=...}}` template in `|description=` (so it ends up as the last line",
          "of the English description). The category line goes at the end of the page.",
          "The JSON file next to this one has, per file, an exact find/replace pair",
          "for the line (unique on the current page) and an append for the category.",
          "",
          "Suggested edit summary: `Add link to the file's Public Lands Institute site page`",
          ""]
    for slug in sorted(by_site, key=lambda k: (k == "(unmatched)", k)):
        recs = by_site[slug]
        site = sites.get(slug)
        md.append(f"## {site['name'] if site else slug} ({len(recs)} files)")
        md.append("")
        lines = {r["add_site_line"] for r in recs if r["add_site_line"]}
        if lines:
            md.append("Add inside the description, before its closing `}}`:")
            md.append("")
            for l in sorted(lines):
                md += ["```", l, "```", ""]
        if any(r["add_category"] for r in recs):
            md += ["Add at the end of the page (files marked [cat] below):", "", "```", CAT_LINE, "```", ""]
        for r in recs:
            flags = []
            if r["add_site_line"]:
                flags.append("line")
            if r["add_category"]:
                flags.append("cat")
            flags += r["notes"]
            link = "https://commons.wikimedia.org/wiki/" + r["title"].replace(" ", "_")
            md.append(f"- [{r['title'][5:]}]({link}) [{', '.join(flags) or 'nothing to add'}]")
        md.append("")
    (OUT_DIR / f"commons_linkback_{today}.md").write_text("\n".join(md))

    current_cat = fetch_page(session, f"Category:{CATEGORY}")
    cat_text = current_cat or ""
    if "__HIDDENCAT__" not in cat_text:
        cat_text = "__HIDDENCAT__\n" + cat_text.lstrip("\n")
    if not current_cat:
        cat_text += ("{{Hiddencat}}\nTracking category for photographs from the "
                     "[https://publiclandsinstitute.net Public Lands Institute], a CC0 "
                     "photographic index of American public lands.\n")
    (OUT_DIR / "Category_Public_Lands_Institute.wikitext").write_text(cat_text)
    (OUT_DIR / "Category_Public_Lands_Institute.current.wikitext").write_text(current_cat or "(page does not exist)\n")

    print(f"{len(todo)} of {len(records)} files need additions; wrote {OUT_DIR}/")


if __name__ == "__main__":
    main()
