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

## What the system works out for you

A rep supplies the formula and the commercial terms. Everything below is
derived, shown in the field it belongs to with an `auto` tag, and **overridden
by typing over it**. Clearing an override hands the field back to the system.

| Derived | From |
|---|---|
| Capsules per serving, servings per bottle, count per bottle | any two give the third |
| Serving size text | capsules per serving |
| Capsule shell | the product's capsule size and material, matched exactly |
| Bottle | capsule volume x count / fill ratio, then the smallest **stocked** size that holds it |
| Closure | the chosen bottle's neck finish, matched exactly |
| Label, neckband, cotton, desiccant, shipper | the single stocked option for each |
| Encapsulation machine | total capsules in the run against the machine table's bands |

Nothing here invents reference data. A bottle is only ever chosen from a size
with purchase-order history, and a machine from a band in the machine table.
Where several options share an identity the system refuses to pick and says so,
because choosing between grades is Purchasing's call.

### Machine selection and work-centre rates

Each production step is costed at its own work centre's rate rather than one
blended rate:

| Step | Rate from |
|---|---|
| Compounding | the blend work centre |
| Encapsulation | the selected machine, with hours from its run rate |
| Bottling | the packaging line |

The machine follows the size of the run, using the thresholds from the
operational price sheet:

| Capsules in run | Machine |
|---|---|
| under 100,000 | Schaefer |
| 100,000 – 400,000 | BOSCH 705 |
| 400,000 – 2,000,000 | BOSCH 1505 |
| 2,000,000 and above | BOSCH 3005 |

### Volume price breaks

Every quote is costed across the volume ladder, each rung a full pipeline run,
so per-batch cost amortises properly and the machine changes with the run size.

**Material cost per bottle is flat across the ladder.** Purchase-order history
carries no volume-tiered pricing, so a discount curve would be invented data.
What moves is labour, overhead and the machine.

### Recommended price

A suggested price per channel from its target margin, where
`margin = (price - cost) / price`:

```
contract   20%   ->  price = cost / 0.80
b2c        80%   ->  price = cost / 0.20
```

These are **recommendations for Sales and Finance**, labelled as such
everywhere they appear. The margin targets in `pricing.csv` are configured
defaults, not a Finance decision — set them before anyone quotes from them.
When cost excludes unresolved lines the recommendation says so, because a
price built on an understated cost is understated too.

---

## Running it

```bash
./run.sh                 # http://127.0.0.1:8000
```

`run.sh` creates `.venv`, installs `requirements.txt` and starts the server.
Needs Python 3.11 or newer.

It runs on macOS, Linux and **Windows under Git Bash**. Windows lays a virtual
environment out as `.venv/Scripts/python.exe` where everything else uses
`.venv/bin/python`, and often has `python` or the `py` launcher rather than
`python3`; the script resolves whichever is present instead of assuming. If no
usable interpreter is found it says so rather than failing further down — the
Microsoft Store `python` stub is detected and rejected, since it is on `PATH`
but is not an interpreter.

Override the bind address with environment variables:

```bash
HOST=0.0.0.0 PORT=9000 ./run.sh
```

Tests:

```bash
.venv/bin/python -m pytest              # macOS and Linux
.venv/Scripts/python -m pytest          # Windows
```

141 tests.

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

### One command

```bash
scripts/load_real_data.sh "Price Sheet.xlsx" Raw_PO.xlsx PKG_PO.xlsx
QUICKQUOTE_REFERENCE_DIR=var/reference ./run.sh
```

The first argument is the workbook holding the inventory master; the rest are
purchase-order exports. Output lands in `var/reference`, which is **not
committed** — those tables carry real vendor pricing, and git history is
forever. The two steps it runs are below if you want them separately.

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

`--merge-into <dir>` appends the proposals to the curated tables in one step,
each row still marked `PROPOSED` so R&D and Purchasing can see what has not
been confirmed. `scripts/load_real_data.sh` does this for you.

**Curation is the remaining work, and the system is honest about it.** Until a
real part is mapped to a canonical identity, the engine will not choose between
candidates — it names them instead:

```
19 stocked closures match a 45mm neck finish (for example KCTP45FDB,
KCTP45BLK, KCTP45BL-VS) - choose one rather than letting the system pick.
```

That is the intended behaviour: child-resistant or not, liner, colour are a
buyer's decision, not the system's.

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
| `labor_rates.csv` | Rates, compounding bands, thresholds, defaults, volume ladder |
| `machines.csv` | Machine, work centre, labor/OH, run rate, capsule band |
| `work_centres.csv` | Labor and OH for compounding and bottling |
| `bulk_density.csv` | Bulk density by identity, then by class, for the fill check |
| `pricing.csv` | Target margin per sales channel |

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
               (matching builds a canonical-identity index over PO rows on
                first use; without it a seven-rung price ladder took seconds)
  api/         app · routes · models
  schemas.py   the one shape every producer feeds and every consumer reads
  store.py     quote registry and artifacts
frontend/      index.html · static/app.js · static/styles.css
tests/         141 tests
samples/       demo quote sheets and generated artifacts
```

---

## Known limits

- **Single-batch production only.** Multi-batch is out of scope, as specified.
- **Manufacturing covers compounding, encapsulation and bottling.** The
  operational price sheet also carries tariffs, freight, remnants, testing by
  ingredient count and customer-specific deductions; those are not modelled here.
- **Machine run rates are placeholders.** The capsules-per-hour figures in
  `machines.csv` are scaled from the one rate that reproduces the
  specification's worked example. Replace them from the `CVC run rates`
  sheet before the manufacturing figure is trusted; the selection bands and
  the labor/OH rates are real.
- **Manufacturing no longer matches the specification's illustration.** The
  spec example assumes one blended labor/OH pair ($29.39 / $79.30); the
  shipped default costs each step at its own work centre, which is what the
  `2026 labOH rates` table describes. Set `use_work_centre_rates` to `0` in
  `labor_rates.csv` to return to the blended pair — the test suite asserts the
  example reproduces to the cent under that setting.
- **The +/-10% range is a worst case, not a distribution.** Every cost-bearing
  line carries the same band, so summing them assumes every input moves the
  same way at once. It is labelled "worst case" rather than treated as a
  confidence interval.
- **Quotes are held in memory** for the life of the process; artifacts are
  written to disk so they survive a restart.
- **No authentication.** Intended to run inside the network.
