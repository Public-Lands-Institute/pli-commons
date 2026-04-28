#!/usr/bin/env python3
"""
regenerate_abstracts.py

Generates new abstracts for Zotero items whose abstracts were cleared by
cleanup_zotero.py, and pushes results directly back to Zotero.

Reads needs_regeneration.json (written by cleanup_zotero.py) to know which
items to process. Uses regen_checkpoint.json for resumability.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 regenerate_abstracts.py \
        --api-key ETzZnKkXmIBOg3VEU62NlBrt
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import anthropic
from pyzotero import zotero

SCRIPT_DIR      = Path(__file__).parent
NEEDS_REGEN     = SCRIPT_DIR / "needs_regeneration.json"
CHECKPOINT      = SCRIPT_DIR / "regen_checkpoint.json"
ERROR_LOG       = SCRIPT_DIR / "regen_errors.log"

MODEL           = "claude-sonnet-4-6"
MAX_TOKENS      = 300
SLEEP_BETWEEN   = 1.5
PROGRESS_EVERY  = 25
ZOTERO_SLEEP    = 0.5

logging.basicConfig(
    filename=ERROR_LOG,
    level=logging.ERROR,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SYSTEM_PROMPT = (
    "You are an academic librarian writing catalog abstracts for a Fine Art "
    "BFA/MFA program reading list. Write a 2–4 sentence scholarly abstract "
    "for the following source. Be accurate and use the existing tags as "
    "context for emphasis. Do not invent specific claims. Write in third "
    "person, present tense. If you cannot reliably summarize this work, "
    "write: [Abstract not available]"
)


def build_prompt(data: dict) -> str:
    def safe(key):
        val = data.get(key, "") or ""
        return str(val).strip()

    tags = "; ".join(t["tag"] for t in data.get("tags", [])) or "none"
    publisher = safe("publisher") or safe("publicationTitle")

    return (
        f"Title: {safe('title')}\n"
        f"Author: {safe('author') or _format_creators(data)}\n"
        f"Year: {safe('date')}\n"
        f"Publisher/Journal: {publisher}\n"
        f"Item Type: {safe('itemType')}\n"
        f"Tags: {tags}"
    )


def _format_creators(data: dict) -> str:
    creators = data.get("creators", [])
    names = []
    for c in creators[:3]:
        last = c.get("lastName", "")
        first = c.get("firstName", "")
        if last:
            names.append(f"{last}, {first}".strip(", "))
        elif c.get("name"):
            names.append(c["name"])
    return "; ".join(names)


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {}


def save_checkpoint(data: dict) -> None:
    with open(CHECKPOINT, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--api-key", required=True, help="Zotero API key")
    return p.parse_args()


def main():
    args = parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    if not NEEDS_REGEN.exists():
        print(f"ERROR: {NEEDS_REGEN} not found.", file=sys.stderr)
        print("Run cleanup_zotero.py first (without --dry-run).", file=sys.stderr)
        sys.exit(1)

    with open(NEEDS_REGEN) as f:
        regen_info = json.load(f)

    library_id   = regen_info["library_id"]
    library_type = regen_info["library_type"]
    target_keys  = set(regen_info["keys"])

    print(f"Keys to regenerate: {len(target_keys)}")
    print(f"Connecting to Zotero (id={library_id}, type={library_type}) …")

    zot = zotero.Zotero(library_id, library_type, args.api_key)
    try:
        zot.count_items()
        print("  Connection OK")
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Fetching target items from Zotero …")
    all_items = zot.everything(zot.top())
    item_map   = {i["key"]: i for i in all_items if i["key"] in target_keys}
    print(f"  Found {len(item_map)} of {len(target_keys)} target items")

    checkpoint = load_checkpoint()
    already_done = set(checkpoint.keys())
    remaining = {k: v for k, v in item_map.items() if k not in already_done}
    print(f"  Checkpoint: {len(already_done)} done previously")
    print(f"  Remaining:  {len(remaining)}")
    print(f"  Model:      {MODEL}  max_tokens={MAX_TOKENS}")
    print()

    client    = anthropic.Anthropic(api_key=anthropic_key)
    generated = 0
    failed    = 0
    start     = time.time()

    for idx, (key, item) in enumerate(remaining.items(), start=1):
        data  = item["data"]
        title = data.get("title", "")[:60]

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(data)}],
            )
            abstract = " ".join(
                b.text for b in response.content if b.type == "text"
            ).strip()

            checkpoint[key] = abstract
            save_checkpoint(checkpoint)
            generated += 1

        except anthropic.APIError as exc:
            msg = f"Key={key}  Title={title!r}  Error: {exc}"
            logging.error(msg)
            print(f"  [ERROR] {msg}")
            failed += 1

        except Exception as exc:
            msg = f"Key={key}  Title={title!r}  Unexpected: {exc}"
            logging.error(msg)
            print(f"  [ERROR] {msg}")
            failed += 1

        # Progress
        total = len(already_done) + generated + failed
        if idx % PROGRESS_EVERY == 0 or idx == len(remaining):
            elapsed = time.time() - start
            pct     = total / len(target_keys) * 100
            rate    = generated / elapsed if elapsed > 0 else 0
            left    = (len(target_keys) - total) / rate if rate > 0 else 0
            print(
                f"  [{idx}/{len(remaining)}]  {total}/{len(target_keys)} "
                f"({pct:.1f}%)  generated={generated}  failed={failed}  "
                f"ETA {str(timedelta(seconds=int(left)))}"
            )

        if idx < len(remaining):
            time.sleep(SLEEP_BETWEEN)

    print(f"\nGeneration done: {generated} generated, {failed} failed.")

    # ── Push to Zotero ────────────────────────────────────────────────────────
    print("\nPushing abstracts to Zotero …")
    print("  Re-fetching items to get latest versions …")
    fresh_items = zot.everything(zot.top())
    item_map = {i["key"]: i for i in fresh_items}

    pushed  = 0
    p_errors = 0

    for key, abstract in checkpoint.items():
        if key not in item_map:
            continue
        item = item_map[key]
        if item["data"].get("abstractNote", "").strip():
            continue   # already filled

        item["data"]["abstractNote"] = abstract
        try:
            zot.update_item(item)
            pushed += 1
            time.sleep(ZOTERO_SLEEP)
        except Exception as exc:
            print(f"  [ERROR] push key={key}: {exc}")
            p_errors += 1

    print(f"  Pushed {pushed} abstracts to Zotero")
    if p_errors:
        print(f"  Push errors: {p_errors} (check {ERROR_LOG})")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Abstracts generated: {generated}")
    print(f"  Generation failures: {failed}")
    print(f"  Pushed to Zotero:    {pushed}")
    if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0:
        print(f"  Error log:           {ERROR_LOG}")
    print("─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
