#!/usr/bin/env python3
"""
backfill_linkback.py: add the Public Lands Institute site-record line to the
files in Category:Public Lands Institute that do not have it yet (finishing
the 2026-11-23 apply_linkback.py run, which stopped at Mammoth Cave 003 on an
uncaught non-JSON API response).

Reads the live category with current wikitext, keeps files whose wikitext
does not contain "publiclandsinstitute.net", maps each to a slug from the PLI
repo (sites.json name + state, confirmed against sites/<slug>.html), checks
each site URL returns 200, and inserts

  <br />Public Lands Institute site record: [URL NAME, STATE]

immediately before the }} that closes {{en|1=...}} inside |description=.
Nothing else in the wikitext changes.

Usage:
  python3 backfill_linkback.py                 # dry run: table + 3 sample diffs
  python3 backfill_linkback.py --apply         # edit (asks nothing; run after approval)
  python3 backfill_linkback.py --apply --limit 1
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

import requests

from upload_next import COMMONS_API, STATE_NAMES, load_env, make_session

HERE = Path(__file__).parent
PLI = HERE.parent / "pli"
CATEGORY = "Category:Public Lands Institute"
SUMMARY = "Add Public Lands Institute site record link"
DELAY = 7          # seconds between edits (task minimum is 6)
MARKER = "publiclandsinstitute.net"
UA = "PLICommonsLinkback/1.0 (https://publiclandsinstitute.net)"
SITE_RX = re.compile(r"Site: (.+?), ([A-Z][a-z]+(?: [A-Z][a-z]+)*)\.")
LOGCSV = HERE / "linkback" / f"backfill_{time.strftime('%Y-%m-%d')}.csv"


def api_json(session, method, **kw):
    """Call the API; retry non-JSON / 5xx responses instead of crashing
    (the 2026-11-23 run died on exactly this)."""
    for attempt in range(5):
        try:
            r = getattr(session, method)(COMMONS_API, timeout=60, **kw)
            return r.json(), r
        except (requests.RequestException, ValueError) as e:
            print(f"   api error ({e.__class__.__name__}), retry {attempt + 1}/5")
            time.sleep(30 * (attempt + 1))
    raise RuntimeError("API unreachable after 5 attempts")


def fetch_category(session):
    p = {"action": "query", "format": "json", "formatversion": "2",
         "generator": "categorymembers", "gcmtitle": CATEGORY, "gcmtype": "file",
         "gcmlimit": "50", "prop": "revisions", "rvprop": "content|ids|timestamp",
         "rvslots": "main", "maxlag": "5"}
    out = {}
    while True:
        d, r = api_json(session, "get", params=p)
        if d.get("error", {}).get("code") == "maxlag":
            time.sleep(int(r.headers.get("Retry-After", 5)))
            continue
        for pg in d.get("query", {}).get("pages", []):
            if pg.get("revisions"):
                rv = pg["revisions"][0]
                out[pg["title"]] = {"revid": rv["revid"], "ts": rv["timestamp"],
                                    "text": rv["slots"]["main"]["content"]}
        if "continue" not in d:
            return out
        p.update(d["continue"])


def live_page(session, title):
    while True:
        d, r = api_json(session, "get", params={
            "action": "query", "prop": "revisions", "rvprop": "content|ids|timestamp",
            "rvslots": "main", "titles": title, "format": "json",
            "formatversion": "2", "maxlag": "5"})
        if d.get("error", {}).get("code") == "maxlag":
            time.sleep(int(r.headers.get("Retry-After", 5)))
            continue
        pg = d["query"]["pages"][0]
        if pg.get("missing") or not pg.get("revisions"):
            return None
        rv = pg["revisions"][0]
        return {"revid": rv["revid"], "ts": rv["timestamp"],
                "text": rv["slots"]["main"]["content"]}


def en_close(text):
    """Index of the }} that closes {{en|1=...}} inside |description=, or an
    error string."""
    starts = [m.start() for m in re.finditer(r"\|description=\{\{en\|1=", text)]
    if len(starts) != 1:
        return f"{len(starts)} '|description={{{{en|1=' matches"
    i = text.index("{{en|1=", starts[0])
    depth, j = 0, i
    while j < len(text) - 1:
        two = text[j:j + 2]
        if two == "{{":
            depth += 1
            j += 2
        elif two == "}}":
            depth -= 1
            if depth == 0:
                return j
            j += 2
        else:
            j += 1
    return "unclosed {{en|1=...}}"


def site_map():
    """(display name, state name) -> slug, from sites.json, each confirmed
    against the generated sites/<slug>.html page."""
    m = {}
    for s in json.load(open(PLI / "sites.json")):
        page = PLI / "sites" / f"{s['slug']}.html"
        if not page.exists():
            continue
        html = page.read_text()
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        h1 = re.sub(r"<[^>]+>", "", h1.group(1)).replace("&amp;", "&").replace("&#x27;", "'").replace("&#39;", "'").strip() if h1 else ""
        m[(s["name"], STATE_NAMES.get(s["state"], s["state"]))] = (s["slug"], h1)
    return m


def plan(pages):
    smap = site_map()
    rows, unmatched = [], []
    for title in sorted(pages):
        pg = pages[title]
        if MARKER in pg["text"]:
            continue
        m = SITE_RX.search(pg["text"])
        if not m:
            unmatched.append((title, "no 'Site: NAME, STATE.' text"))
            continue
        name, state = m.group(1), m.group(2)
        hit = smap.get((name, state))
        if not hit:
            unmatched.append((title, f"no sites.json entry for {name!r}, {state}"))
            continue
        slug, h1 = hit
        if h1 and h1 != name:
            unmatched.append((title, f"sites/{slug}.html h1 is {h1!r}, not {name!r}"))
            continue
        url = f"https://publiclandsinstitute.net/sites/{slug}.html"
        line = f"<br />Public Lands Institute site record: [{url} {name}, {state}]"
        rows.append({"title": title, "site": name, "state": state, "slug": slug,
                     "url": url, "line": line})
    return rows, unmatched


def insert(text, line):
    """Return (new_text, None) or (None, reason)."""
    k = en_close(text)
    if isinstance(k, str):
        return None, k
    new = text[:k] + line + text[k:]
    # the change must be exactly this one inserted line and nothing else
    if not (new.startswith(text[:k]) and new.endswith(text[k:])
            and len(new) - len(text) == len(line)
            and text.count(MARKER) == 0 and new.count(MARKER) == 1
            and new.count("\n") == text.count("\n")):
        return None, "diff is not exactly the one inserted line"
    return new, None


def check_urls(rows):
    status = {}
    s = requests.Session()
    s.headers["User-Agent"] = UA
    for url in sorted({r["url"] for r in rows}):
        try:
            status[url] = s.get(url, timeout=30, allow_redirects=False).status_code
        except requests.RequestException as e:
            status[url] = f"error {e.__class__.__name__}"
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    ro = requests.Session()
    ro.headers["User-Agent"] = UA
    pages = fetch_category(ro)
    rows, unmatched = plan(pages)
    status = check_urls(rows)
    print(f"{len(pages)} files in {CATEGORY}; {len(rows) + len(unmatched)} lack the link")

    # per-site table
    sites = OrderedDict()
    for r in rows:
        sites.setdefault((r["site"], r["slug"]), []).append(r)
    print(f"\n{'site':45} {'slug':45} url  files")
    for (site, slug), rs in sites.items():
        print(f"{site:45} {slug:45} {status[rs[0]['url']]!s:4} {len(rs):5}")
    print(f"{'':91}{sum(len(v) for v in sites.values()):6}  ({len(sites)} sites)")
    bad = {u for u, c in status.items() if c != 200}
    if unmatched or bad:
        print("\nNOT EDITED (needs you):")
        for t, why in unmatched:
            print(f"  {t}: {why}")
        for u in sorted(bad):
            print(f"  {u}: status {status[u]}")
    rows = [r for r in rows if r["url"] not in bad]

    if not a.apply:
        picks, seen = [], set()
        for want in ("Mammoth Cave National Park", "New River Gorge National Park and Preserve",
                     "St. Anne's Woods and Wetlands"):
            picks += [r for r in rows if r["site"] == want][:1]
        for r in picks:
            old = pages[r["title"]]["text"]
            new, err = insert(old, r["line"])
            k = en_close(old)
            print(f"\n=== {r['title']}  (rev {pages[r['title']]['revid']})")
            if err:
                print(f"  SKIP: {err}")
                continue
            ol = [l for l in old.split("\n") if l.startswith("|description=")][0]
            nl = [l for l in new.split("\n") if l.startswith("|description=")][0]
            print(f"- {ol}\n+ {nl}")
            print(f"  inserted at char {k}, +{len(new) - len(old)} chars, all other lines identical")
        # check every planned insert would pass
        fails = [(r["title"], insert(pages[r["title"]]["text"], r["line"])[1]) for r in rows]
        fails = [f for f in fails if f[1]]
        print(f"\nInsert check on all {len(rows)} planned files: {len(rows) - len(fails)} pass, {len(fails)} would be skipped")
        for t, why in fails:
            print(f"  {t}: {why}")
        print("\nDry run only: nothing edited.")
        return

    load_env()
    user = os.environ["COMMONS_USERNAME"]
    bot = os.environ.get("COMMONS_BOT_NAME")
    session, csrf = make_session(f"{user}@{bot}" if bot else user,
                                 os.environ["COMMONS_BOT_PASSWORD"])
    if a.limit:
        rows = rows[:a.limit]
    LOGCSV.parent.mkdir(exist_ok=True)
    new_file = not LOGCSV.exists()
    f = open(LOGCSV, "a", newline="")
    w = csv.writer(f)
    if new_file:
        w.writerow(["file", "site", "slug", "status", "revision_id", "reason"])
    counts = {"edited": 0, "skipped": 0, "failed": 0}
    reasons = []
    for t, why in unmatched:
        w.writerow([t, "", "", "skipped", "", why]); counts["skipped"] += 1; reasons.append(why)
    for i, r in enumerate(rows):
        title = r["title"]
        pg = live_page(session, title)
        if pg is None:
            res = ("skipped", "", "page missing")
        elif MARKER in pg["text"]:
            res = ("skipped", pg["revid"], "link already present")
        else:
            new, err = insert(pg["text"], r["line"])
            if err:
                res = ("skipped", pg["revid"], err)
            else:
                while True:
                    d, resp = api_json(session, "post", data={
                        "action": "edit", "title": title, "text": new, "summary": SUMMARY,
                        "baserevid": pg["revid"], "basetimestamp": pg["ts"],
                        "nocreate": 1, "maxlag": 5, "token": csrf, "format": "json"})
                    if d.get("error", {}).get("code") == "maxlag":
                        wait = int(resp.headers.get("Retry-After", 5))
                        print(f"   maxlag, waiting {wait}s")
                        time.sleep(wait)
                        continue
                    break
                if d.get("edit", {}).get("result") == "Success":
                    res = ("edited", d["edit"].get("newrevid", ""), "")
                else:
                    e = d.get("error", {})
                    res = ("failed", pg["revid"], f"{e.get('code')}: {e.get('info')}")
        st, rev, why = res
        counts[st] += 1
        if why:
            reasons.append(why)
        w.writerow([title, r["site"], r["slug"], st, rev, why]); f.flush()
        print(f"{st:8} {rev!s:11} {title}{'  (' + why + ')' if why else ''}")
        if st == "edited" and i < len(rows) - 1:
            time.sleep(DELAY)
    f.close()
    print(f"\nedited {counts['edited']}, skipped {counts['skipped']}, failed {counts['failed']}")
    from collections import Counter
    for why, n in Counter(reasons).most_common():
        print(f"  {n:4}  {why}")
    print(f"log: {LOGCSV}")


if __name__ == "__main__":
    main()
