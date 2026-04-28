# SOA Research Database — Zotero Pipeline

Automated enrichment and organization tools for the SOA Research Database Zotero group library. Built for the BFA/MFA Fine Art program reading list.

---

## What these scripts do

| Script | Purpose |
|---|---|
| `cleanup_zotero.py` | Normalize tag capitalization, clear junk abstracts, flag missing years / duplicates / out-of-scope items |
| `fill_metadata.py` | Fill missing author, year, and publisher via CrossRef, Open Library, and Google Books |
| `regenerate_abstracts.py` | Generate scholarly abstracts for items that had theirs cleared by cleanup |
| `generate_tags.py` | Use Claude to suggest subject tags for untagged items, constrained to the existing tag taxonomy |
| `organize_collections.py` | Create theme sub-collections under "Themes" and assign items based on their tags |
| `clean_data.py` | **Ongoing workflow** — enriches new items placed in the Zotero "Temp" collection end-to-end |
| `run_maintenance.sh` | Shell script that runs the full maintenance pipeline in order |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Add this to your `~/.zshrc` or `~/.bash_profile` to make it permanent.

### 3. Zotero API key

The scripts use a hardcoded Zotero API key and library ID for the SOA Research Database group library. If you need to change these, edit the constants at the top of each script:

```python
LIBRARY_ID   = "2767253"
LIBRARY_TYPE = "group"
```

---

## Workflow 1 — Initial / Full Library Cleanup

Run this once to enrich the full library. Each script is safe to re-run (they skip work already done).

### Step 1 — Normalize and flag

```bash
python3 cleanup_zotero.py \
  --library-id 2767253 \
  --library-type group \
  --api-key YOUR_ZOTERO_KEY \
  --dry-run          # remove --dry-run when ready
```

What it does:
- Clears `[Abstract not available]` placeholders and table-of-contents junk pasted as abstracts
- Merges case-duplicate tags (e.g. "photography" + "Photography" → one canonical form)
- Tags items missing a publication year with `needs-year`
- Tags exact title duplicates with `possible-duplicate`
- Tags likely out-of-scope items with `needs-review`
- Saves a list of keys with cleared abstracts to `needs_regeneration.json`

### Step 2 — Fill missing metadata

```bash
python3 fill_metadata.py \
  --library-id 2767253 \
  --library-type group \
  --api-key YOUR_ZOTERO_KEY \
  --dry-run
```

Looks up missing author, year, and publisher via CrossRef (journals/articles), Open Library (books), and Google Books. Only fills blank fields — never overwrites existing data. Caches results to `metadata_cache.json` so reruns are fast.

### Step 3 — Regenerate cleared abstracts

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 regenerate_abstracts.py --api-key YOUR_ZOTERO_KEY
```

Reads `needs_regeneration.json` (written by Step 1) and uses Claude to write a 2–4 sentence scholarly abstract for each item. Checkpoints to `regen_checkpoint.json` — safe to stop and resume.

### Step 4 — Tag untagged items

```bash
python3 generate_tags.py --api-key YOUR_ZOTERO_KEY
```

Finds items with no subject tags, pulls the existing tag taxonomy from the library, and asks Claude to suggest 3–6 tags per item using only the existing taxonomy. Checkpoints to `tag_checkpoint.json`.

### Step 5 — Create theme collections and assign items

```bash
python3 organize_collections.py --api-key YOUR_ZOTERO_KEY --dry-run
python3 organize_collections.py --api-key YOUR_ZOTERO_KEY
```

Creates 16 subject-area sub-collections under a "Themes" parent collection and assigns every item to the appropriate collection(s) based on its tags. Items can belong to multiple collections.

**The 16 themes:**
- Critical Theory & Philosophy
- Art History & Movements
- Contemporary Art Practice
- Photography & the Image
- Politics, Power & Capital
- Gender, Feminism & Queer Theory
- Race, Colonialism & Diaspora
- Identity, Body & Subjectivity
- Technology, Media & Digital Culture
- Ecology & the Anthropocene
- Futures & Speculation
- Popular Culture & Counterculture
- Fiction & Literature
- Pedagogy & Institutional Critique
- Space, Place & the Built World
- Violence, Extremism & the Political Unconscious

### Or: run all maintenance steps at once

```bash
export ANTHROPIC_API_KEY=sk-ant-...
bash run_maintenance.sh

# dry run first:
bash run_maintenance.sh --dry-run
```

---

## Workflow 2 — Ongoing: Clean New Items (Recommended)

When you add new items to Zotero, place them in a collection called **"Temp"** and run:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 clean_data.py --api-key YOUR_ZOTERO_KEY --dry-run
python3 clean_data.py --api-key YOUR_ZOTERO_KEY
```

For each item in Temp, this script:
1. Fills missing author, year, and publisher (CrossRef → Open Library → Google Books)
2. Generates a scholarly abstract via Claude (if blank)
3. Generates subject tags via Claude using the library's existing taxonomy
4. Normalizes tag capitalization
5. Assigns the item to the appropriate theme collection(s)
6. **Removes the item from Temp** when done

After it finishes, sync Zotero desktop (green sync button).

---

## Manual review

After running the pipeline, filter by these tags in Zotero to do a manual pass:

| Tag | Meaning |
|---|---|
| `needs-year` | Publication year could not be found automatically |
| `possible-duplicate` | Exact title match found elsewhere in the library |
| `needs-review` | Flagged as potentially out of scope (films, podcasts, fiction signals) |

---

## Files created by the scripts

| File | Purpose |
|---|---|
| `needs_regeneration.json` | Keys of items whose abstracts were cleared (input for regenerate_abstracts.py) |
| `regen_checkpoint.json` | Progress checkpoint for regenerate_abstracts.py |
| `tag_checkpoint.json` | Progress checkpoint for generate_tags.py |
| `metadata_cache.json` | Cache of CrossRef/Open Library/Google Books results |
| `regen_errors.log` | Errors from regenerate_abstracts.py |

These files are local only and not committed to git.

---

## Costs

- **Zotero API**: Free for group libraries
- **CrossRef / Open Library / Google Books**: Free, no key required
- **Anthropic (Claude)**: ~$0.003 per abstract or tag set at Sonnet rates. A full library of ~1,200 items costs roughly $3–5 total.
