# Quick Quote System

An internal decision-support tool that turns customer quote sheets and finished
product specifications into a **review-ready quote package**.

It exists to cut quote turnaround time, surface review-worthy issues early, and
give Sales, Finance, R&D and Purchasing a consistent starting point for pricing
decisions.

> Quick Quote **prepares** quote packages. It does not finalise pricing, replace
> human review, or act as a system of record.

---

## The rule that shapes everything

All cost math, matching, flag generation and confidence scoring is
**deterministic**. Document parsing for unstructured inputs may be assisted, but
no assisted output is ever used for a cost value, a match decision, a confidence
level or a flag.

Concretely, the engine never invents data:

| Situation | What the system does |
|---|---|
| Identity cannot be resolved | Line is left **Unmatched**. No forced match. |
| Several PO records share one identity | Line is **Needs Review**; cost excluded from the primary total. |
| Size is not stated, or does not match | Unmatched. 175cc never becomes 250cc; size 00 never becomes size 0. |
| Potency unknown | 1.0 applied **and an R&D flag raised naming the default**. |
| Overage class unknown | 5% applied **and an R&D flag raised**. |
| Volume not given | Manufacturing simply is not estimated. |
| Amount stated in IU | Claimed mg recorded as **missing** — there is no conversion basis on file. |
| A field is absent from the document | Recorded in `missing_fields`. Never filled in. |

Only **Accepted** lines flow into primary cost. Everything else is reported
separately as cost exposure, and cost columns for those lines read `Excluded`,
never an estimate.

---

## Running it

```bash
./run.sh                 # http://127.0.0.1:8000
```

`run.sh` creates `.venv`, installs `requirements.txt` and starts the server.
Python 3.11+.

```bash
.venv/bin/python -m pytest        # 86 tests
```

---

## What the web app does

**Build a quote** — the sales-rep interface. Pick ingredients and packaging from
the reference catalogue, set the format, serving and volume, and the cost panel
updates live. Every figure on that panel comes from `POST /api/quotes/preview`,
which runs the *same* pipeline as the final package — so what a rep sees while
editing is what the generated workbook says. Preview stores nothing.

Each catalogue entry shows its expected outcome before it is added (Accepted /
Needs Review / Unmatched) and the PO record behind it, so a rep knows a line will
be a problem before quoting it rather than after.

Size-bearing components (bottles, caps, capsule shells) are chosen from the
sizes that actually exist on file, because size is never substituted.

**Upload a quote sheet** — drop an `xlsx`, `xlsm`, `pdf`, `docx`, `txt` or `csv`.
*Inspect parse only* shows exactly what was read and what was missing before any
costing happens.

**Quote package** — the full result: BOM, packaging, manufacturing, owner-keyed
flags, and downloads.

**Recent quotes**, **Reference data** — the quote log, and every rate,
threshold, overage class and guard pair the engine uses, so nothing is hidden.

### Outputs

- **Six-tab Excel workbook** — Product Summary, BOM / Raw Materials, Packaging,
  Cost Summary, Review Flags, Customer Quote Summary.
- **Customer quote PDF** — marked *Internal Draft* until Sales and Finance review.

---

## How a quote is built

```
parse → match → cost → flag → confidence-score
```

**Matching** runs in tiers; the first that succeeds decides the result.

1. **Exact part code** against PO history → Accepted.
2. **Identity match** — the description must resolve to the same canonical
   identity as a PO record's description. One record → Accepted. Several →
   Needs Review. For packaging, size must match exactly.
3. **Nothing matched** → Unmatched.

Identity is resolved by substring containment of a defined alias. Aliases under
five characters need word boundaries and may never match through the compacted
form — which is what stops `msm` colliding with `mm smooth silver`. When several
aliases hit, the longest wins. **No fuzzy similarity scoring is used anywhere.**

**The distinct-identity guard list** is checked after every resolution. A
description that reaches across a guarded pair — `Turmeric Powder and Garlic
Powder Blend`, `Glucosamine Sulfate and HCl` — is ambiguous by definition and is
never matched.

**Costing**

```
formula mg/serving = claimed mg / potency × (1 + overage)
kg/bottle          = formula mg/serving × servings per bottle / 1e6 × yield loss
cost/bottle        = kg/bottle × latest PO cost per kg
```

`min_unit_cost_ever` and `max_unit_cost_ever` are reported as context only; they
never drive the range. The ±10% band applies to PO-derived material and
packaging cost. Manufacturing is computed from labor and overhead rates rather
than PO history, so it carries no band and enters both ends of the range
unchanged.

**Confidence** — per line: High is an exact part-code match on a PO under 365
days old; Medium is an identity match or a stale exact match; Low is anything
not Accepted. Quote level is Low if any line is outstanding, otherwise driven by
the major-spend lines (those over 10% of primary cost).

**Flags** are owned by exactly one function — Purchasing, R&D, Operations, Sales
or Finance. Owners are never collapsed, reordered or renamed.

---

## Loading real data

The shipped `backend/quickquote/reference/data/` is a **worked demonstration
set**, calibrated to reproduce the example in the system specification. It is
not production pricing. Point the system at real data with two commands.

### 1. Build PO history from operational exports

The operational PO exports are transaction level — `Purchase Order Date`, `Part
Number`, `Order Qty`, `Unit Cost` — with no description and no vendor.
Descriptions come from the inventory master.

```bash
PYTHONPATH=backend .venv/bin/python -m quickquote.reference.ingest \
    --po "Raw_PO.xlsx" --po "PKG_PO.xlsx" \
    --item-master "Price Sheet.xlsx" \
    --out backend/quickquote/reference/data/po_history.csv
```

It finds the inventory-master sheet itself, aggregates transactions per part
(latest cost by latest date, min, max, PO count), and **skips report subtotal
rows explicitly, reporting how many** rather than dropping them silently.

Vendor is absent from those exports, so `latest_vendor` and
`unique_vendor_count` are left blank and the ingest says so. The single-supplier
flag therefore stays silent rather than firing on every line — an empty column
must not read as "one vendor".

### 2. Propose identity rows from the real catalogue

Identity tables are curated reference data. This proposes rows so R&D and
Purchasing start from the catalogue that exists, not a blank file:

```bash
PYTHONPATH=backend .venv/bin/python -m quickquote.reference.seed \
    --po-history backend/quickquote/reference/data/po_history.csv \
    --out-dir /tmp/proposed
```

Every proposal is conservative by construction: one identity per distinct
description so nothing is merged across grades; two parts whose descriptions
normalise identically land on one identity and will correctly report as Needs
Review; potency is left blank so the documented default applies *and* raises its
R&D flag; and descriptions already covered by curation are skipped so
hand-curated entries are never displaced.

**Nothing is applied automatically.** Review the proposals, then append the
confirmed rows to the curated tables.

Point the app at a different reference directory with
`QUICKQUOTE_REFERENCE_DIR=/path/to/data`.

### Reference tables

| File | Holds |
|---|---|
| `po_history.csv` | Part, description, UoM, latest/min/max cost, latest PO date, vendor, counts |
| `ingredient_identity.csv` | Canonical ingredient → aliases, overage class, potency |
| `packaging_identity.csv` | Canonical packaging → aliases, role, whether size is required |
| `distinct_guard.csv` | Identity pairs that must never be matched |
| `overage.csv` | Overage % by class, multi- and single-ingredient |
| `potency.csv` | Part code → potency factor |
| `capsule_fill.csv` | Capsule size → mg capacity |
| `labor_rates.csv` | Labor and OH rates, compounding bands, thresholds, defaults |

`uom` extends the specified PO export format (`KG`, `EA`, `M` for per-thousand).
It is optional and inferred when absent, and it is what lets capsule shells be
priced per thousand and the unit-of-measure sanity check work.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Status and reference row counts |
| `GET` | `/api/reference/overview` | Rates, thresholds, guard pairs, overage classes |
| `GET` | `/api/reference/ingredients` \| `/packaging` | Selectable catalogues with PO backing |
| `POST` | `/api/reference/reload` | Re-read the reference tables from disk |
| `POST` | `/api/quotes/preview` | Cost a quote **without** storing it |
| `POST` | `/api/quotes` | Generate and store a quote package |
| `POST` | `/api/quotes/upload` | Parse a document and cost it |
| `POST` | `/api/quotes/parse-only` | Parse a document and return what was read |
| `GET` | `/api/quotes` \| `/api/quotes/{id}` | Quote log, one quote |
| `GET` | `/api/quotes/{id}/workbook.xlsx` \| `/quote.pdf` | Artifacts |

Interactive docs at `/docs`.

---

## Layout

```
backend/quickquote/
  engine/      text · identity · matching · classify · costing ·
               manufacturing · flags · confidence · pipeline
  parsing/     readers (xlsx/pdf/docx/txt/csv) · extract · router
  output/      workbook (six tabs) · pdf
  reference/   loader · catalog · ingest · seed · data/*.csv
  api/         app · routes · models
  schemas.py   the one shape every producer feeds and every consumer reads
  store.py     quote registry and artifacts
frontend/      index.html · static/app.js · static/styles.css
tests/         86 tests
samples/       demo quote sheets and generated artifacts
```

---

## Known limits

- **Single-batch production only.** Multi-batch is out of scope, as specified.
- **Manufacturing covers compounding, encapsulation and bottling.** The
  operational price sheet also carries tariffs, freight, remnants, testing by
  ingredient count and customer-specific deductions; those are not modelled here.
- **Labor and OH are single rates.** The operational `2026 labOH rates` table is
  per work centre (Blend, 705, 1505, 3005, Schaefer, Tablet press …). The shipped
  default is the Blend rate ($29.39 labor / $79.30 OH), matching the
  specification.
- **Quotes are held in memory** for the life of the process; artifacts are
  written to disk so they survive a restart.
- **No authentication.** Intended to run inside the network.
