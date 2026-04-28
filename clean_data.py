#!/usr/bin/env python3
"""
clean_data.py  —  SOA Research Database: New Item Pipeline

Processes items placed in the Zotero "Temp" collection and runs them
through the full enrichment pipeline:

  1. Fill missing metadata (author, year, publisher) via CrossRef,
     Open Library, and Google Books
  2. Generate a scholarly abstract via Claude (if blank)
  3. Generate subject tags via Claude (if none present)
  4. Normalize tag capitalization against the library taxonomy
  5. Assign to theme collections based on tags
  6. Remove from "Temp" when done

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 clean_data.py --api-key ETzZnKkXmIBOg3VEU62NlBrt
    python3 clean_data.py --api-key ETzZnKkXmIBOg3VEU62NlBrt --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import anthropic
from pyzotero import zotero

# ── Config ────────────────────────────────────────────────────────────────────
LIBRARY_ID   = "2767253"
LIBRARY_TYPE = "group"
TEMP_NAME    = "Temp"          # name of the staging collection in Zotero
SCRIPT_DIR   = Path(__file__).parent

SLEEP_API     = 1.0    # between CrossRef / Open Library / Google Books calls
SLEEP_CLAUDE  = 1.5    # between Anthropic calls
SLEEP_ZOTERO  = 0.4    # between Zotero write calls

CROSSREF_EMAIL = "jordan.tate@gmail.com"
FLAG_TAGS = {"needs-year", "possible-duplicate", "needs-review"}

MODEL_ABSTRACT = "claude-sonnet-4-6"
MODEL_TAGS     = "claude-sonnet-4-6"
MAX_TOKENS_ABSTRACT = 300
MAX_TOKENS_TAGS     = 150

ABSTRACT_SYSTEM = (
    "You are an academic librarian writing catalog abstracts for a Fine Art "
    "BFA/MFA program reading list. Write a 2–4 sentence scholarly abstract "
    "for the following source. Be accurate and use the existing tags as "
    "context for emphasis. Do not invent specific claims. Write in third "
    "person, present tense. If you cannot reliably summarize this work, "
    "write: [Abstract not available]"
)

TAG_SYSTEM = (
    "You are a cataloguer for a Fine Art BFA/MFA program reading list. "
    "Given a bibliographic entry, suggest 3–6 tags that best describe its "
    "subject matter. Choose ONLY from the provided tag list — do not invent "
    "new tags. Return tags as a comma-separated list on a single line, nothing else."
)

# ── Theme → tag mapping (used for collection assignment) ─────────────────────
THEMES = {
    "Critical Theory & Philosophy": {
        "Philosophy", "theory", "Theory & Criticism", "critique", "Phenomenology",
        "Postmodernism", "semiotics", "poststructuralism", "epistemology", "ontology",
        "existentialism", "speculative realism", "Metaphysics", "social theory",
        "cultural theory", "critical theory", "Aesthetics", "ethics",
        "classical theory", "contemporary theory", "object-oriented ontology",
        "speculative philosophy", "flat ontology", "nonhuman theory",
        "philosophy of mind", "anti-realism", "materialism", "structuralism",
        "systems theory", "discourse", "Post-Structuralism", "post-structuralism",
        "OOO & Speculative Realism", "Consciousness & Cognition",
        "agential realism", "new materialism",
    },
    "Art History & Movements": {
        "modernism", "art history", "historiography", "Movements", "formalism",
        "conceptual art", "minimalism", "Fluxus", "Fluxus-adjacent",
        "exhibition history", "Art", "Aesthetics", "neo-avant-garde",
        "post-minimalism", "post-conceptual", "art theory", "art writing",
        "criticism", "art world", "American modernism", "modern art",
        "post-historical art", "exhibition design", "canon",
        "medium specificity", "medium", "realism", "abstraction", "Classics",
    },
    "Contemporary Art Practice": {
        "contemporary art", "social practice", "performance", "drawing",
        "sculpture", "sculpture_expanded", "sculpture_object", "installation",
        "installation art", "post-medium condition", "conceptual practice",
        "post-studio practice", "contemporary practice", "performativity",
        "instruction", "instruction-based art", "instruction art",
        "body art", "land art", "site-specificity", "site-specific art",
        "site-responsiveness", "public art", "new genre public art",
        "socially engaged art", "relational practice", "participatory art",
        "collaboration", "process", "practice", "appropriation", "painting",
        "experimental practice", "sound art", "sound", "endurance",
        "maintenance art", "walking art", "artistic experimentation",
        "contemporary sculpture", "artists writings", "art writing",
        "post-internet", "internet art", "tactical media",
    },
    "Photography & the Image": {
        "photography", "visual culture", "representation", "documentary",
        "indexicality", "mediation", "spectatorship", "archive",
        "circulation", "images", "perception", "visual theory",
        "visuality", "visual studies", "self-portraiture", "staged",
        "agency of images", "networked images", "war imagery",
        "depiction", "aura", "simulation", "spectacle",
        "digital circulation", "digital archives",
        "Essay films", "Experimental films",
    },
    "Politics, Power & Capital": {
        "Politics & Social Sciences", "Capitalism", "Ideology", "Marx & Leftism",
        "power", "labor", "political economy", "Neoliberalism", "political theory",
        "politics", "Globalization", "political form", "political imagination",
        "political critique", "political art", "social reproduction",
        "immaterial labor", "digital labor", "precarity", "class",
        "anarchism", "Socialism", "Populism", "Radicalism", "democracy",
        "governance", "sovereignty", "Acceleration", "accelerationism",
        "geopolitics", "nationalism", "infrastructure", "policy", "citizenship",
        "political essay",
    },
    "Gender, Feminism & Queer Theory": {
        "Feminism", "Gender & Queerness", "LGBT", "feminist theory",
        "queer theory", "feminist art", "performativity", "gender",
        "gender theory", "feminist philosophy", "Third-wave feminism",
        "sexual difference", "feminist critique", "feminist spatial practice",
        "trans studies", "sexuality", "camp", "masculinity", "desire",
    },
    "Race, Colonialism & Diaspora": {
        "Race", "colonialism", "decolonial theory", "black studies",
        "afrofuturism", "postcolonial theory", "indigenous studies",
        "decolonial practice", "postcolonial critique", "black radical aesthetics",
        "black imagination", "fugitivity", "primitivism", "orientalism",
        "colonial modernism", "displacement", "exile",
    },
    "Identity, Body & Subjectivity": {
        "subjectivity", "embodiment", "identity", "affect", "psychoanalysis",
        "memory", "Psychology & Psychoanalysis", "cognition",
        "Identity & Self", "psychology", "Mental Illness", "trauma",
        "mourning", "mortality", "presence", "experience", "introspection",
        "Disability", "behavior", "habit",
    },
    "Technology, Media & Digital Culture": {
        "art & tech", "Computers & Digital", "media theory", "Internet",
        "post-internet", "network culture", "Social Media", "digital culture",
        "Big Tech & Silicon Valley", "Information Systems", "Technology",
        "Cyber-prefix", "cyberculture", "cyberpunk", "cybernetics",
        "Science & Technology", "media", "Media & Communications",
        "digital theory", "post-digital", "post-digital theory",
        "post-digital culture", "new media", "software studies",
        "digital production", "internet culture", "internet history",
        "internet art", "AI", "AI & Singularity", "virtual reality",
        "computation", "networks", "database", "digital aesthetics",
        "digital mediation", "immersive media", "glitch",
        "technoscience", "actor-network theory", "science studies",
    },
    "Ecology & the Anthropocene": {
        "ecology", "landscape", "Anthropocene", "climate change",
        "Environment & Ecology", "land art", "Natural World & Physics",
        "climate", "climate theory", "environmental justice", "ocean studies",
        "coexistence", "walking", "place",
    },
    "Futures & Speculation": {
        "Science Fiction/Speculative", "Posthumanism", "Utopia", "Dystopia",
        "afrofuturism", "speculative realism", "Endtimes", "Acceleration",
        "The Future", "speculative fiction", "speculative futures",
        "speculative technology", "speculative aesthetics",
        "speculative culture", "speculative politics", "speculative theory",
        "speculative philosophy", "worldbuilding", "future imaginaries",
        "futurity", "utopian thought", "Aliens", "Paranormal",
        "Myth/Legend/Folklore", "OOO & Speculative Realism", "science fiction",
    },
    "Popular Culture & Counterculture": {
        "Popular Culture", "Music", "Counterculture", "Fandom",
        "Social Media", "Fandom & Subculture", "Electronic Music",
        "Rave", "Subculture", "subcultures", "consumer culture",
        "mass culture", "Parasocial Relationships",
        "Occult & Esotericism", "Conspiracism & Fringe", "fashion", "Culture",
    },
    "Fiction & Literature": {
        "Fiction", "Memoir", "Mystery/Thriller/Suspense", "Fantasy",
        "Horror", "Literature", "Science Fiction/Speculative",
        "postmodern literature", "contemporary fiction",
        "narrative", "literary theory", "literary history",
        "Tragedy", "Biography", "Classics", "absurdism",
        "contemporary writing", "language", "Language & Linguistics",
        "text", "voice", "rhetoric",
    },
    "Pedagogy & Institutional Critique": {
        "pedagogy", "art education", "institutional critique", "collaboration",
        "social practice", "activism", "radical pedagogy", "Education",
        "curriculum", "Critical pedagogy", "Critical thinking",
        "interdisciplinarity", "Methodology", "knowledge", "literacy",
        "media literacy", "visual literacy", "social change",
        "participation", "dialogue", "social cooperation", "art activism",
    },
    "Space, Place & the Built World": {
        "urbanism", "site-specificity", "public art", "architecture",
        "spatial practice", "land art", "landscape", "walking",
        "site", "social space", "public space", "domestic space",
        "place", "spatial politics", "spatial representation",
        "spatial theory", "new topographics", "cartography",
        "mapping", "civic space", "flanerie", "mobility",
        "site-specific art", "site-responsiveness", "spatiality", "Planning",
    },
    "Violence, Extremism & the Political Unconscious": {
        "Extremism", "Fascism & Totalitarianism", "Conspiracism & Fringe",
        "Violence", "Terrorism", "Shooters", "Military & War",
        "Crime", "Disaster", "crisis", "security", "surveillance",
        "war imagery",
    },
}


# ── HTTP helper ───────────────────────────────────────────────────────────────

def get_json(url: str, timeout: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"PLICommons/1.0 (mailto:{CROSSREF_EMAIL})"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


# ── Title matching ────────────────────────────────────────────────────────────

def _titles_match(a: str, b: str) -> bool:
    stop = {"the", "a", "an", "of", "in", "and", "or"}
    aw = set(a.split()) - stop
    bw = set(b.split()) - stop
    if not aw or not bw:
        return False
    return len(aw & bw) / max(len(aw), len(bw)) >= 0.6


# ── Metadata lookups ──────────────────────────────────────────────────────────

def crossref_lookup(title: str, author: str = "") -> dict | None:
    q = urllib.parse.quote(f"{title} {author}".strip())
    url = f"https://api.crossref.org/works?query={q}&rows=1&select=title,author,published,publisher,type"
    data = get_json(url)
    if not data:
        return None
    items = data.get("message", {}).get("items", [])
    if not items:
        return None
    item = items[0]
    if not _titles_match(title.lower(), " ".join(item.get("title", [""])).lower()):
        return None
    authors = [
        f"{a.get('family','')}, {a.get('given','')}".strip(", ")
        for a in item.get("author", []) if a.get("family")
    ]
    parts = item.get("published", {}).get("date-parts", [[]])
    year = str(parts[0][0]) if parts and parts[0] else ""
    return {"authors": authors, "year": year, "publisher": item.get("publisher", "")}


def openlibrary_lookup(title: str, author: str = "") -> dict | None:
    q = urllib.parse.quote(f"{title} {author}".strip())
    url = f"https://openlibrary.org/search.json?q={q}&limit=1&fields=title,author_name,first_publish_year,publisher"
    data = get_json(url)
    if not data:
        return None
    docs = data.get("docs", [])
    if not docs:
        return None
    doc = docs[0]
    if not _titles_match(title.lower(), doc.get("title", "").lower()):
        return None
    raw = doc.get("author_name", [])
    authors = []
    for a in raw[:3]:
        p = a.strip().rsplit(" ", 1)
        authors.append(f"{p[1]}, {p[0]}" if len(p) == 2 else a)
    pubs = doc.get("publisher", [])
    return {
        "authors": authors,
        "year": str(doc.get("first_publish_year", "")),
        "publisher": pubs[0] if pubs else "",
    }


def googlebooks_lookup(title: str, author: str = "") -> dict | None:
    q = urllib.parse.quote(f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else ""))
    url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults=1&fields=items(volumeInfo)"
    data = get_json(url)
    if not data:
        return None
    items = data.get("items", [])
    if not items:
        return None
    info = items[0].get("volumeInfo", {})
    if not _titles_match(title.lower(), info.get("title", "").lower()):
        return None
    raw = info.get("authors", [])
    authors = []
    for a in raw[:3]:
        p = a.strip().rsplit(" ", 1)
        authors.append(f"{p[1]}, {p[0]}" if len(p) == 2 else a)
    year = info.get("publishedDate", "")[:4]
    return {"authors": authors, "year": year, "publisher": info.get("publisher", "")}


def lookup_metadata(title: str, author_hint: str, item_type: str) -> dict | None:
    if item_type in ("journalArticle", "conferencePaper", "report"):
        return (crossref_lookup(title, author_hint)
                or openlibrary_lookup(title, author_hint)
                or googlebooks_lookup(title, author_hint))
    return (openlibrary_lookup(title, author_hint)
            or googlebooks_lookup(title, author_hint)
            or crossref_lookup(title, author_hint))


# ── Zotero helpers ────────────────────────────────────────────────────────────

def get_existing_authors(data: dict) -> list[str]:
    return [
        f"{c.get('lastName','')}, {c.get('firstName','')}".strip(", ")
        for c in data.get("creators", [])
        if c.get("lastName") or c.get("firstName")
    ]


def authors_to_creators(authors: list[str], creator_type: str = "author") -> list[dict]:
    out = []
    for a in authors:
        if "," in a:
            last, first = a.split(",", 1)
            out.append({"creatorType": creator_type, "lastName": last.strip(), "firstName": first.strip()})
        else:
            out.append({"creatorType": creator_type, "name": a.strip()})
    return out


# ── Claude helpers ────────────────────────────────────────────────────────────

def build_abstract_prompt(data: dict) -> str:
    def safe(k): return str(data.get(k, "") or "").strip()
    creators = data.get("creators", [])
    authors = "; ".join(
        f"{c.get('lastName','')}, {c.get('firstName','')}".strip(", ")
        for c in creators[:3] if c.get("lastName") or c.get("firstName")
    )
    tags = "; ".join(t["tag"] for t in data.get("tags", [])) or "none"
    return (
        f"Title: {safe('title')}\n"
        f"Author: {authors}\n"
        f"Year: {safe('date')}\n"
        f"Publisher/Journal: {safe('publisher') or safe('publicationTitle')}\n"
        f"Item Type: {safe('itemType')}\n"
        f"Tags: {tags}"
    )


def build_tag_prompt(data: dict, tag_list: list[str]) -> str:
    def safe(k): return str(data.get(k, "") or "").strip()
    creators = data.get("creators", [])
    authors = "; ".join(
        f"{c.get('lastName','')}, {c.get('firstName','')}".strip(", ")
        for c in creators[:3] if c.get("lastName") or c.get("firstName")
    )
    return (
        f"Title: {safe('title')}\n"
        f"Author: {authors}\n"
        f"Year: {safe('date')}\n"
        f"Publisher/Journal: {safe('publisher') or safe('publicationTitle')}\n"
        f"Item Type: {safe('itemType')}\n"
        f"Abstract: {safe('abstractNote')[:400]}\n\n"
        f"Tag list:\n{', '.join(tag_list)}"
    )


def is_junk_abstract(text: str) -> bool:
    if not text or text.strip() == "[Abstract not available]":
        return False
    t = text.strip()
    if t.count(" -- ") > 3:
        return True
    if re.match(r"^(Chapter \d|Introduction :|Part \d)", t):
        return True
    if len(t) < 60 and not re.search(r"[.!?]", t):
        return True
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Clean and enrich items in the Zotero Temp collection")
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

    # ── Find the Temp collection ─────────────────────────────────────────────
    all_collections = zot.everything(zot.collections())
    temp_col = next((c for c in all_collections if c["data"]["name"] == TEMP_NAME), None)
    if not temp_col:
        print(f"ERROR: No collection named '{TEMP_NAME}' found in Zotero.", file=sys.stderr)
        print("Create a collection called 'Temp' and add the items you want to process.", file=sys.stderr)
        sys.exit(1)
    temp_key = temp_col["key"]
    print(f"Found '{TEMP_NAME}' collection (key={temp_key})")

    # ── Fetch items from Temp ────────────────────────────────────────────────
    temp_items = zot.everything(zot.collection_items_top(temp_key))
    print(f"  {len(temp_items)} items to process\n")
    if not temp_items:
        print("Nothing in Temp — add items and rerun.")
        return

    # ── Build tag taxonomy from full library ─────────────────────────────────
    print("Building tag taxonomy from library …")
    all_items = zot.everything(zot.top())
    tag_counts: dict[str, int] = {}
    for item in all_items:
        for t in item["data"].get("tags", []):
            tag = t["tag"]
            if tag not in FLAG_TAGS:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    # Lower-case map for normalization
    lower_to_canonical: dict[str, str] = {}
    for tag in tag_counts:
        lower_to_canonical.setdefault(tag.lower(), tag)
    tag_list = sorted(k for k, v in tag_counts.items() if v >= 2)
    tag_set_lower = {t.lower() for t in tag_list}
    print(f"  {len(tag_list)} tags in taxonomy\n")

    # ── Build collection key map ─────────────────────────────────────────────
    col_by_name = {c["data"]["name"]: c["key"] for c in all_collections}
    themes_parent = col_by_name.get("Themes")
    theme_col_keys: dict[str, str] = {}
    if themes_parent:
        for c in all_collections:
            if c["data"].get("parentCollection") == themes_parent and c["data"]["name"] in THEMES:
                theme_col_keys[c["data"]["name"]] = c["key"]
    tag_to_themes: dict[str, set[str]] = {}
    for theme_name, tags in THEMES.items():
        for tag in tags:
            tag_to_themes.setdefault(tag.lower(), set()).add(theme_name)

    client = anthropic.Anthropic(api_key=anthropic_key)

    stats = {"metadata": 0, "abstract": 0, "tags": 0, "collections": 0, "removed_temp": 0}

    for idx, item in enumerate(temp_items, 1):
        data  = item["data"]
        key   = item["key"]
        title = str(data.get("title", "") or "")
        print(f"\n[{idx}/{len(temp_items)}] {key}  {title[:60]}")
        changed = False

        # ── 1. Fill missing metadata ─────────────────────────────────────────
        existing_authors = get_existing_authors(data)
        missing_author    = not existing_authors
        missing_year      = not str(data.get("date", "") or "").strip()
        missing_publisher = not str(data.get("publisher", "") or "").strip()

        if (missing_author or missing_year or missing_publisher) and title:
            author_hint = existing_authors[0] if existing_authors else ""
            result = lookup_metadata(title, author_hint, data.get("itemType", ""))
            time.sleep(SLEEP_API)

            if result:
                if missing_author and result.get("authors"):
                    ct = "bookAuthor" if data.get("itemType") == "bookSection" else "author"
                    data["creators"] = data.get("creators", []) + authors_to_creators(result["authors"], ct)
                    print(f"  + author: {', '.join(result['authors'][:2])}")
                    stats["metadata"] += 1
                    changed = True
                if missing_year and result.get("year"):
                    data["date"] = result["year"]
                    print(f"  + year: {result['year']}")
                    changed = True
                if missing_publisher and result.get("publisher"):
                    field = "publicationTitle" if data.get("itemType") == "journalArticle" else "publisher"
                    if not str(data.get(field, "") or "").strip():
                        data[field] = result["publisher"]
                        print(f"  + publisher: {result['publisher']}")
                        changed = True

        # ── 2. Generate abstract ─────────────────────────────────────────────
        abstract = str(data.get("abstractNote", "") or "").strip()
        if not abstract or is_junk_abstract(abstract):
            try:
                resp = client.messages.create(
                    model=MODEL_ABSTRACT,
                    max_tokens=MAX_TOKENS_ABSTRACT,
                    system=ABSTRACT_SYSTEM,
                    messages=[{"role": "user", "content": build_abstract_prompt(data)}],
                )
                new_abstract = " ".join(b.text for b in resp.content if b.type == "text").strip()
                if new_abstract and new_abstract != "[Abstract not available]":
                    data["abstractNote"] = new_abstract
                    print(f"  + abstract ({len(new_abstract)} chars)")
                    stats["abstract"] += 1
                    changed = True
                time.sleep(SLEEP_CLAUDE)
            except Exception as exc:
                print(f"  [ERROR] abstract: {exc}")

        # ── 3. Generate tags ─────────────────────────────────────────────────
        subject_tags = [t["tag"] for t in data.get("tags", []) if t["tag"] not in FLAG_TAGS]
        if not subject_tags:
            try:
                resp = client.messages.create(
                    model=MODEL_TAGS,
                    max_tokens=MAX_TOKENS_TAGS,
                    system=TAG_SYSTEM,
                    messages=[{"role": "user", "content": build_tag_prompt(data, tag_list)}],
                )
                raw = " ".join(b.text for b in resp.content if b.type == "text").strip()
                new_tags = []
                for t in raw.split(","):
                    t = t.strip().strip(".")
                    if t.lower() in tag_set_lower:
                        canonical = lower_to_canonical.get(t.lower(), t)
                        new_tags.append(canonical)
                if new_tags:
                    existing_tag_set = {t["tag"] for t in data.get("tags", [])}
                    data["tags"] = data.get("tags", []) + [
                        {"tag": t} for t in new_tags if t not in existing_tag_set
                    ]
                    print(f"  + tags: {', '.join(new_tags)}")
                    stats["tags"] += 1
                    changed = True
                time.sleep(SLEEP_CLAUDE)
            except Exception as exc:
                print(f"  [ERROR] tags: {exc}")

        # ── 4. Normalize tag capitalization ──────────────────────────────────
        tags = data.get("tags", [])
        normalized = []
        tag_changed = False
        seen: set[str] = set()
        for t in tags:
            canonical = lower_to_canonical.get(t["tag"].lower(), t["tag"])
            if canonical not in seen:
                seen.add(canonical)
                normalized.append({"tag": canonical})
                if canonical != t["tag"]:
                    tag_changed = True
        if tag_changed:
            data["tags"] = normalized
            print("  normalized tag capitalization")
            changed = True

        # ── 5. Write metadata + tags to Zotero ──────────────────────────────
        if changed and not args.dry_run:
            try:
                zot.update_item(item)
                time.sleep(SLEEP_ZOTERO)
            except Exception as exc:
                print(f"  [ERROR] update: {exc}")

        # ── 6. Assign to theme collections ───────────────────────────────────
        if theme_col_keys:
            current_cols = set(data.get("collections", []))
            item_tag_lower = {t["tag"].lower() for t in data.get("tags", [])}
            matched_themes = set()
            for tag in item_tag_lower:
                if tag in tag_to_themes:
                    matched_themes |= tag_to_themes[tag]

            new_col_keys = {
                theme_col_keys[t] for t in matched_themes
                if t in theme_col_keys and theme_col_keys[t] not in current_cols
            }
            if new_col_keys:
                print(f"  → collections: {', '.join(t for t in matched_themes if theme_col_keys.get(t) in new_col_keys)}")
                if not args.dry_run:
                    # Re-fetch to get latest version before updating collections
                    fresh = zot.item(key)
                    fresh["data"]["collections"] = list(
                        set(fresh["data"].get("collections", [])) | new_col_keys
                    )
                    try:
                        zot.update_item(fresh)
                        stats["collections"] += 1
                        time.sleep(SLEEP_ZOTERO)
                    except Exception as exc:
                        print(f"  [ERROR] collections: {exc}")
                else:
                    stats["collections"] += 1

        # ── 7. Remove from Temp ──────────────────────────────────────────────
        if not args.dry_run:
            try:
                fresh = zot.item(key)
                cols = set(fresh["data"].get("collections", []))
                cols.discard(temp_key)
                fresh["data"]["collections"] = list(cols)
                zot.update_item(fresh)
                stats["removed_temp"] += 1
                print("  removed from Temp")
                time.sleep(SLEEP_ZOTERO)
            except Exception as exc:
                print(f"  [ERROR] remove from Temp: {exc}")
        else:
            print("  [dry-run] would remove from Temp")

    print(f"\n── Summary ──────────────────────────────────────────────────")
    print(f"  Items processed:          {len(temp_items)}")
    print(f"  Metadata filled:          {stats['metadata']}")
    print(f"  Abstracts generated:      {stats['abstract']}")
    print(f"  Tag sets generated:       {stats['tags']}")
    print(f"  Assigned to collections:  {stats['collections']}")
    print(f"  Removed from Temp:        {stats['removed_temp']}")
    if args.dry_run:
        print("\n  DRY RUN — rerun without --dry-run to commit.")
    else:
        print("\n  Sync your Zotero desktop app.")
    print(f"─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
