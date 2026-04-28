#!/usr/bin/env bash
# run_maintenance.sh
#
# Runs the full SOA Research Database maintenance pipeline in order:
#   1. cleanup_zotero.py   — normalize tags, clear junk abstracts, flag issues
#   2. fill_metadata.py    — fill missing author/year/publisher (CrossRef, Open Library, Google Books)
#   3. generate_tags.py    — tag untagged items using Claude
#   4. organize_collections.py — assign items to theme collections
#
# Prerequisites:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   pip install -r requirements.txt
#
# Usage:
#   bash run_maintenance.sh
#   bash run_maintenance.sh --dry-run

set -euo pipefail

ZOTERO_API_KEY="ETzZnKkXmIBOg3VEU62NlBrt"
LIBRARY_ID="2767253"
LIBRARY_TYPE="group"
DRY_RUN="${1:-}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set."
  echo "Run: export ANTHROPIC_API_KEY=sk-ant-..."
  exit 1
fi

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  echo "=== DRY RUN MODE — no changes will be written ==="
  DRY=""
  DR_FLAG="--dry-run"
else
  DRY=""
  DR_FLAG=""
fi

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Step 1/4 — Cleanup: normalize tags, clear junk"
echo "══════════════════════════════════════════════════════"
python3 "$DIR/cleanup_zotero.py" \
  --library-id "$LIBRARY_ID" \
  --library-type "$LIBRARY_TYPE" \
  --api-key "$ZOTERO_API_KEY" \
  $DR_FLAG

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Step 2/4 — Fill metadata (CrossRef / Open Library / Google Books)"
echo "══════════════════════════════════════════════════════"
python3 "$DIR/fill_metadata.py" \
  --library-id "$LIBRARY_ID" \
  --library-type "$LIBRARY_TYPE" \
  --api-key "$ZOTERO_API_KEY" \
  $DR_FLAG

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Step 3/4 — Generate tags for untagged items (Claude)"
echo "══════════════════════════════════════════════════════"
python3 "$DIR/generate_tags.py" \
  --api-key "$ZOTERO_API_KEY" \
  $DR_FLAG

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Step 4/4 — Assign items to theme collections"
echo "══════════════════════════════════════════════════════"
python3 "$DIR/organize_collections.py" \
  --api-key "$ZOTERO_API_KEY" \
  $DR_FLAG

echo ""
echo "══════════════════════════════════════════════════════"
echo "  All done. Sync your Zotero desktop app."
echo "══════════════════════════════════════════════════════"
