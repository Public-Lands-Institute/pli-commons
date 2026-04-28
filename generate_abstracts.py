#!/usr/bin/env python3
"""
generate_abstracts.py

Generates scholarly abstracts for Zotero bibliography entries that are missing
an Abstract Note, using the Anthropic API. Progress is saved to a checkpoint
file after every entry so the script is safe to kill and restart.

Usage:
    pip install anthropic pandas
    export ANTHROPIC_API_KEY=sk-ant-...
    python generate_abstracts.py

Output files (written next to this script):
    abstracts_checkpoint.json  — incremental progress (key → abstract text)
    SOA_Research_Final.csv     — merged final CSV
    abstracts_errors.log       — API failures
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import pandas as pd

# ── Configuration ────────────────────────────────────────────────────────────

# The user requested claude-sonnet-4-20250514; that model is deprecated
# (retiring June 15 2026). Using the current equivalent instead.
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 300
SLEEP_BETWEEN = 1.5       # seconds → ~40 req/min
PROGRESS_EVERY = 25       # print progress banner every N entries

SCRIPT_DIR = Path(__file__).parent
CSV_INPUT   = Path("/Users/jordan/Desktop/SOA Research Database.csv")
CHECKPOINT  = SCRIPT_DIR / "abstracts_checkpoint.json"
CSV_OUTPUT  = SCRIPT_DIR / "SOA_Research_Final.csv"
ERROR_LOG   = SCRIPT_DIR / "abstracts_errors.log"

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    filename=ERROR_LOG,
    level=logging.ERROR,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are an academic librarian writing catalog abstracts for a Fine Art "
    "BFA/MFA program reading list. Write a 2–4 sentence scholarly abstract "
    "for the following source. Be accurate and use the existing tags as "
    "context for emphasis. Do not invent specific claims. Write in third "
    "person, present tense. If you cannot reliably summarize this work, "
    "write: [Abstract not available]"
)

def build_user_prompt(row: pd.Series) -> str:
    def safe(col: str) -> str:
        val = row.get(col, "")
        return str(val).strip() if pd.notna(val) and str(val).strip() not in ("", "nan") else ""

    tags_parts = [safe("Generated_Tags"), safe("Manual Tags")]
    tags = " | ".join(t for t in tags_parts if t) or "none"

    publisher = safe("Publisher") or safe("Publication Title")

    return (
        f"Title: {safe('Title')}\n"
        f"Author: {safe('Author')}\n"
        f"Year: {safe('Publication Year')}\n"
        f"Publisher/Journal: {publisher}\n"
        f"Item Type: {safe('Item Type')}\n"
        f"Tags: {tags}"
    )

# ── Checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint() -> dict[str, str]:
    if CHECKPOINT.exists():
        with open(CHECKPOINT, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_checkpoint(data: dict[str, str]) -> None:
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── API call ──────────────────────────────────────────────────────────────────

def generate_abstract(client: anthropic.Anthropic, row: pd.Series) -> str:
    """Call the API; return abstract text or '[Abstract not available]'."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(row)}],
    )
    text_blocks = [b.text for b in response.content if b.type == "text"]
    return " ".join(text_blocks).strip()

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading CSV: {CSV_INPUT}")
    df = pd.read_csv(CSV_INPUT, dtype=str)

    key_col = df.columns[0]  # "Key"

    # Rows that need a generated abstract
    needs_abstract = (
        df["Abstract Note"].isna() | (df["Abstract Note"].str.strip() == "")
    )
    todo_df = df[needs_abstract].copy()
    already_have = int((~needs_abstract).sum())

    print(f"  Total rows:        {len(df)}")
    print(f"  Have abstract:     {already_have}")
    print(f"  Need abstract:     {len(todo_df)}")

    checkpoint = load_checkpoint()
    already_done = set(checkpoint.keys())
    remaining = todo_df[~todo_df[key_col].isin(already_done)]

    print(f"  Checkpoint loaded: {len(already_done)} done previously")
    print(f"  Remaining to run:  {len(remaining)}")
    print(f"  Model:             {MODEL}  max_tokens={MAX_TOKENS}")
    print()

    if remaining.empty:
        print("Nothing left to generate — merging checkpoint into final CSV.")
    else:
        client = anthropic.Anthropic(api_key=api_key)

        generated = 0
        failed = 0
        start_time = time.time()

        for idx, (_, row) in enumerate(remaining.iterrows(), start=1):
            key = str(row[key_col]).strip()
            title = str(row.get("Title", "")).strip()

            try:
                abstract = generate_abstract(client, row)
                checkpoint[key] = abstract
                save_checkpoint(checkpoint)
                generated += 1

            except anthropic.APIError as exc:
                msg = f"Key={key}  Title={title!r}  Error: {exc}"
                logging.error(msg)
                print(f"  [ERROR] {msg}")
                failed += 1

            except Exception as exc:  # noqa: BLE001
                msg = f"Key={key}  Title={title!r}  Unexpected: {exc}"
                logging.error(msg)
                print(f"  [ERROR] {msg}")
                failed += 1

            # Progress banner
            total_processed = len(already_done) + generated + failed
            total_needed = len(todo_df)
            if idx % PROGRESS_EVERY == 0 or idx == len(remaining):
                elapsed = time.time() - start_time
                pct = total_processed / total_needed * 100
                rate = generated / elapsed if elapsed > 0 else 0
                remaining_count = total_needed - total_processed
                eta_s = remaining_count / rate if rate > 0 else 0
                eta_str = str(timedelta(seconds=int(eta_s)))
                print(
                    f"  [{idx}/{len(remaining)}]  total {total_processed}/{total_needed} "
                    f"({pct:.1f}%)  generated={generated}  failed={failed}  "
                    f"ETA {eta_str}"
                )

            if idx < len(remaining):
                time.sleep(SLEEP_BETWEEN)

        print(f"\nGeneration complete: {generated} generated, {failed} failed.")
        if failed:
            print(f"  Failures logged to: {ERROR_LOG}")

    # ── Merge checkpoint → CSV ────────────────────────────────────────────────

    print("\nMerging abstracts into CSV …")
    if "Generated_Abstract" not in df.columns:
        df["Generated_Abstract"] = ""

    merged = 0
    for key, abstract in checkpoint.items():
        mask = df[key_col] == key
        if mask.any():
            # Only fill rows where Abstract Note was originally empty
            empty_mask = mask & (df["Abstract Note"].isna() | (df["Abstract Note"].str.strip() == ""))
            df.loc[empty_mask, "Abstract Note"] = abstract
            df.loc[empty_mask, "Generated_Abstract"] = abstract
            merged += empty_mask.sum()

    df.to_csv(CSV_OUTPUT, index=False, quoting=csv.QUOTE_ALL)
    print(f"  Abstracts merged:  {merged}")
    print(f"  Saved final CSV:   {CSV_OUTPUT}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────────────")
    total_generated = len(checkpoint)
    skipped = already_have
    print(f"  Already had abstract (skipped):  {skipped}")
    print(f"  Generated (total in checkpoint): {total_generated}")
    failed_count = len(todo_df) - total_generated
    print(f"  Failed / not generated:          {max(0, failed_count)}")
    print(f"  Final CSV: {CSV_OUTPUT}")
    if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > 0:
        print(f"  Error log: {ERROR_LOG}")
    print("─────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
