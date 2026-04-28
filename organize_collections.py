#!/usr/bin/env python3
"""
organize_collections.py

Creates theme sub-collections under a top-level "Themes" collection
in Zotero, then assigns items to them based on tags.

Usage:
    python3 organize_collections.py \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt \
        --dry-run        # remove when ready to commit
"""

import argparse
import sys
import time
from pyzotero import zotero

LIBRARY_ID   = "2767253"
LIBRARY_TYPE = "group"
SLEEP        = 0.35

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
        "History and criticism", "Art, Modern",
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
        "trans studies", "sexuality", "camp", "masculinity",
        "desire", "Identity & Self",
    },
    "Race, Colonialism & Diaspora": {
        "Race", "colonialism", "decolonial theory", "black studies",
        "afrofuturism", "postcolonial theory", "indigenous studies",
        "decolonial practice", "postcolonial critique", "black radical aesthetics",
        "black imagination", "fugitivity", "primitivism", "orientalism",
        "colonial modernism", "displacement", "exile",
        "contemporary Asian art",
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
        "Information technology",
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


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--api-key", required=True, help="Zotero API key")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

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

    # ── Step 1: Get or create "Themes" parent ───────────────────────────────
    print("Fetching existing collections …")
    existing = zot.everything(zot.collections())
    existing_by_name = {c["data"]["name"]: c["key"] for c in existing}

    if "Themes" in existing_by_name:
        themes_parent_key = existing_by_name["Themes"]
        print(f"  Found existing 'Themes' parent (key={themes_parent_key})")
    else:
        print("  Creating 'Themes' parent collection …")
        if not args.dry_run:
            result = zot.create_collections([{"name": "Themes", "parentCollection": False}])
            themes_parent_key = list(result["success"].values())[0]
            time.sleep(SLEEP)
        else:
            themes_parent_key = "DRY_THEMES"
        print(f"  Created 'Themes' (key={themes_parent_key})")

    # ── Step 2: Create missing sub-collections ──────────────────────────────
    existing_subs = {
        c["data"]["name"]: c["key"]
        for c in existing
        if c["data"].get("parentCollection") == themes_parent_key
    }

    collection_keys = {}
    created = 0

    print(f"\nEnsuring {len(THEMES)} theme sub-collections exist …")
    for name in THEMES:
        if name in existing_subs:
            collection_keys[name] = existing_subs[name]
            print(f"  exists  {name}")
        else:
            print(f"  create  {name}")
            if not args.dry_run:
                result = zot.create_collections([{
                    "name": name,
                    "parentCollection": themes_parent_key,
                }])
                collection_keys[name] = list(result["success"].values())[0]
                time.sleep(SLEEP)
            else:
                collection_keys[name] = f"DRY_{name[:6]}"
            created += 1

    print(f"\n  {created} new sub-collections created, {len(THEMES) - created} already existed")

    # ── Step 3: Build tag → themes lookup (case-insensitive) ────────────────
    tag_to_themes = {}
    for theme_name, tags in THEMES.items():
        for tag in tags:
            tag_to_themes.setdefault(tag.lower(), set()).add(theme_name)

    # ── Step 4: Assign items ─────────────────────────────────────────────────
    print("\nFetching all top-level items …")
    all_items = zot.everything(zot.top())
    print(f"  {len(all_items)} items\n")

    assigned_count = 0
    already_count  = 0
    unmatched      = 0

    for idx, item in enumerate(all_items, 1):
        data  = item["data"]
        title = data.get("title", "")[:60]
        item_tags = {t["tag"].lower() for t in data.get("tags", [])}
        current_keys = set(data.get("collections", []))

        matched_themes = set()
        for tag in item_tags:
            if tag in tag_to_themes:
                matched_themes |= tag_to_themes[tag]

        if not matched_themes:
            unmatched += 1
            continue

        new_keys = {
            collection_keys[t] for t in matched_themes
            if collection_keys[t] not in current_keys
        }

        if not new_keys:
            already_count += 1
            continue

        added_themes = sorted(t for t in matched_themes if collection_keys[t] in new_keys)
        print(f"[{item['key']}] {title}")
        for t in added_themes:
            print(f"  → {t}")

        if not args.dry_run:
            data["collections"] = list(current_keys | new_keys)
            try:
                zot.update_item(item)
                assigned_count += 1
                time.sleep(SLEEP)
            except Exception as exc:
                print(f"  ERROR: {exc}")
        else:
            assigned_count += 1

        if idx % 100 == 0:
            print(f"  [{idx}/{len(all_items)}]  assigned={assigned_count}  unmatched={unmatched}")

    print(f"\n── Summary ──────────────────────────────────────────────────")
    print(f"  Theme sub-collections created:       {created}")
    print(f"  Items assigned to ≥1 new collection: {assigned_count}")
    print(f"  Items already correctly placed:      {already_count}")
    print(f"  Items with no matching tags:         {unmatched}")
    if args.dry_run:
        print("\n  DRY RUN — rerun without --dry-run to commit.")
    print(f"─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
