#!/usr/bin/env bash
# Build the reference tables from DrVita's operational exports.
#
#   scripts/load_real_data.sh <price-sheet.xlsx> <raw-po.xlsx> [pkg-po.xlsx ...]
#
# The first file is the workbook holding the inventory master (the sheet with
# PART and DESCRIPTION columns); the rest are purchase-order exports.
#
# Output goes to a directory that is NOT committed, because these tables carry
# real vendor pricing. Start the app against it with:
#
#   QUICKQUOTE_REFERENCE_DIR=var/reference ./run.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -lt 2 ]; then
  sed -n '2,13p' "$0" >&2
  exit 2
fi

MASTER="$1"; shift
TARGET="${QUICKQUOTE_REFERENCE_DIR:-var/reference}"

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
[ -e "$PY" ] || { echo "No virtual environment found. Run ./run.sh once first." >&2; exit 1; }

for file in "$MASTER" "$@"; do
  [ -f "$file" ] || { echo "File not found: $file" >&2; exit 1; }
done

echo "==> Seeding $TARGET from the shipped reference tables"
mkdir -p "$TARGET"
cp backend/quickquote/reference/data/*.csv "$TARGET/"

PO_ARGS=()
for file in "$@"; do PO_ARGS+=(--po "$file"); done

echo
echo "==> Aggregating purchase-order history against the inventory master"
PYTHONPATH=backend "$PY" -m quickquote.reference.ingest \
  "${PO_ARGS[@]}" --item-master "$MASTER" --out "$TARGET/po_history.csv"

echo
echo "==> Proposing identities for parts the curated tables do not cover"
PYTHONPATH=backend "$PY" -m quickquote.reference.seed \
  --po-history "$TARGET/po_history.csv" \
  --out-dir "$TARGET/proposed" \
  --merge-into "$TARGET"

echo
echo "==> Done."
echo "    Start the app against this data with:"
echo "      QUICKQUOTE_REFERENCE_DIR=$TARGET ./run.sh"
echo "    Proposed identities are in $TARGET/proposed for R&D and Purchasing to review."
