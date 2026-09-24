#!/usr/bin/env bash
# Build the reference tables from DrVita's operational exports.
#
#   scripts/load_real_data.sh <price-sheet.xlsx> <raw-po.xlsx> [pkg-po.xlsx ...]
#
# The first file is the workbook holding the inventory master (the sheet with
# PART and DESCRIPTION columns); the rest are purchase-order exports.
#
# R&D's four reference workbooks are loaded too when they are found in the
# directory named by QUICKQUOTE_RND_DIR (default: the current directory).
# Without them the shipped overage and capsule tables are used and the
# part-keyed potency and density tables are simply absent.
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

# The shipped tables declare themselves demonstration data, and every quote
# costed against them is stamped not-for-quoting. This set is about to be
# rebuilt from DrVita's own exports, so it stops being a demonstration.
PYTHONPATH=backend "$PY" - "$TARGET" <<'MARK'
import csv, sys
from datetime import date
from pathlib import Path

path = Path(sys.argv[1]) / "labor_rates.csv"
rows = list(csv.DictReader(path.open()))
fields = list(rows[0])
label = f"DrVita operational data loaded {date.today():%Y-%m-%d}"
values = {"dataset_is_demonstration": "0", "dataset_label": label}
for row in rows:
    if row["key"] in values:
        row["value"] = values.pop(row["key"])
for key, value in values.items():
    rows.append({**{name: "" for name in fields}, "key": key, "value": value})
with path.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
print(f"    marked as operational data: {label}")
MARK

PO_ARGS=()
for file in "$@"; do PO_ARGS+=(--po "$file"); done

echo
echo "==> Aggregating purchase-order history against the inventory master"
PYTHONPATH=backend "$PY" -m quickquote.reference.ingest \
  "${PO_ARGS[@]}" --item-master "$MASTER" --out "$TARGET/po_history.csv"

RND_DIR="${QUICKQUOTE_RND_DIR:-.}"
RND_ARGS=()
for pair in "overage:Overage_Guidelines" "potency:Potency_of_Material" \
            "testing:RM_Testing" "capsules:Capsule_Size__Calculator"; do
  flag="${pair%%:*}"
  # R&D export these from Google Sheets, so the names pick up suffixes.
  found=$(find "$RND_DIR" -maxdepth 1 -name "*${pair#*:}*.xlsx" 2>/dev/null | head -1)
  if [ -n "$found" ]; then RND_ARGS+=(--"$flag" "$found"); fi
done

if [ "${#RND_ARGS[@]}" -gt 0 ]; then
  echo
  echo "==> Loading R&D's reference workbooks from $RND_DIR"
  PYTHONPATH=backend "$PY" -m quickquote.reference.ingest_rnd \
    "${RND_ARGS[@]}" --out-dir "$TARGET"
else
  echo
  echo "==> No R&D workbooks found in $RND_DIR - using the shipped tables."
  echo "    Set QUICKQUOTE_RND_DIR to the folder holding Overage_Guidelines.xlsx,"
  echo "    Potency_of_Material.xlsx, RM_Testing.xlsx and Capsule_Size__Calculator.xlsx."
fi

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
