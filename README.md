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

### Process routes — one per dosage form

Manufacturing is not capsule-only. Each dosage form has a **route** in
`process_routes.csv`, and each step names the work centre that runs it:

| Form | Route |
|---|---|
| Capsule | Compounding → Blend clean → Encapsulation → Encap clean → Bottling |
| Tablet | Compounding → Granulation → Tablet press → Blend clean → Bottling |
| Powder | Compounding → Powder fill → Blend clean → IPF clean |
| Packet | Compounding → Packet fill → Packet clean |
| Softgel | Compounding → Encapsulation → Bottling |
| Gummy | Compounding → Depositing → Curing → Bottling |

Adding a line is a row in a CSV, not a code change.

**A step whose rate or throughput is not on file is never estimated and never
silently dropped.** It is named, reported to Operations, and the total is
marked incomplete — so a tablet cannot come back priced as though pressing
were free. Steps are marked *core* or *ancillary*: an uncosted **core** step
(a press, a fill) means manufacturing is **not estimated at all** and
contributes nothing; an uncosted **ancillary** step (cleaning) only
understates the total and says so.

All 15 work centres from the `2026 labOH rates` table are loaded, including
the five cleaning rates. Gummies have no work centre in that table, so a gummy
quote refuses to estimate manufacturing and names the three steps it cannot
cost.

### Analytical testing

Testing is costed from the price sheet's ingredient-count bands — 1–3
ingredients $700/batch + $0.35/unit, 4–5 $900 + $0.45, 6+ $1,100 + $0.55 — and
appears as its own line in the breakdown.

### Machine selection and work-centre rates

Each production step is costed at its own work centre's rate rather than one
blended rate:

| Step | Rate from |
|---|---|
| Compounding | the blend work centre |
| Encapsulation | the selected machine, with hours from its run rate |
| Bottling | the packaging line, at its confirmed rate for this bottle count |

**Labor is per operator.** Packaging runs four operators on PKG1 and five on
PKG2&3, so an hour on that line costs four or five times its labor rate;
overhead is per machine hour and is not multiplied. Operations have not said
which line a given product runs on, so the quote names the one it assumed and
what the other would have cost.

**Set up and cleaning are paid once per batch**, alongside compounding. Set up
is two hours on an encapsulator, one on a packaging line, three on rapid pack.
Cleaning follows the machine: two hours on the 705, four on the 1505, six on
the 3005.

The machine table holds **hard-capsule encapsulators only**, so a softgel or
gummy run selects none rather than borrowing a capsule machine's rate.

### Batches

A run is split into batches by blender capacity, which is a *volume* limit, so
the weight it holds depends on the blend's density:

| Blender | Usable | at 0.4 g/mL | at 0.6 g/mL |
|---|---|---|---|
| 300 L | 240 L | 96 kg | 144 kg |
| 600 L | 480 L | 192 kg | 288 kg |
| 1,400 L | 1,120 L | 448 kg | 672 kg |
| 1,800 L | 1,440 L | 576 kg | 864 kg |
| 8,500 L | 6,800 L | 2,720 kg | 4,080 kg |

Where every material in the formula has a measured density the blend's own is
used; where one does not, Operations' quoting assumption of 0.4 g/mL applies,
which gives the smaller batch. The smallest blender that holds the whole run in
one batch is chosen; a run that fits in none is split across batches of the
largest.

This matters most at volume. Compounding, set up and cleaning are per-batch
costs, and a single-batch model lets them amortise towards zero as the order
grows. They cannot: **manufacturing cost per bottle is about 50% higher at
100,000 bottles and above** than the single-batch model said.

The machine follows the size of the run, using the thresholds from the
operational price sheet:

| Capsules in run | Machine | Speed |
|---|---|---|
| under 100,000 | Schaefer | 8,000/hr |
| 100,000 – 400,000 | BOSCH 705 | 32,000/hr |
| 400,000 – 2,000,000 | BOSCH 1505 | 75,000/hr |
| 2,000,000 and above | BOSCH 3005 | 110,000/hr |

### Volume price breaks

Every quote is costed across the volume ladder, each rung a full pipeline run,
so per-batch cost amortises properly and the machine changes with the run size.

**Material cost per bottle is flat across the ladder.** Purchase-order history
carries no volume-tiered pricing, so a discount curve would be invented data.
What moves is labour, overhead and the machine.

### Recommended price

A suggested price per channel from its target margin, where
`margin = (price - cost) / price`:

Finance set the requirement per account, and an account can face two at once
on two different cost bases:

| Account | Margin |
|---|---|
| Walmart, Costco | 30% including overhead |
| Nature's Lab | 65% |
| Every other account | 20% including overhead **and** 30% excluding it |

Materials, packaging and analytical testing are purchases and carry no
allocated overhead, so the only thing the second basis strips is
manufacturing's overhead share. The two are different tests: on a typical
capsule the 30%-excluding rule is the tighter one, and the quote marks
whichever binds.

**A tablet, powder or stick pack may be quoted down to 20% whichever account
it is.** The floor only ever relaxes a requirement — it never raises one, so
a standard account already at 20% is unaffected.

These are **recommendations for Sales and Finance**, labelled as such
everywhere they appear: the rules say what a price has to clear, not what to
charge. A rule not yet confirmed by Finance raises a blocking flag, and a
quote with no customer on it says so rather than quietly applying the
standard terms — Walmart and Costco are held higher. When cost excludes
unresolved lines the recommendation says so, because a price built on an
understated cost is understated too.

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

356 tests.

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

**Overage and potency come from R&D's own sheets**, loaded by
`quickquote.reference.ingest_rnd`:

```bash
PYTHONPATH=backend .venv/bin/python -m quickquote.reference.ingest_rnd \
  --overage  Overage_Guidelines.xlsx \
  --potency  Potency_of_Material.xlsx \
  --testing  RM_Testing.xlsx \
  --capsules Capsule_Size__Calculator.xlsx \
  --out-dir  var/reference
```

Four things about those sheets shape the engine:

*Overage has four columns, not two.* R&D price multi- and single-ingredient
formulas separately, and the same again for gummies, where depositing and
curing cost far more potency: vitamin C is 20%/10% in a capsule and 40%/30% in
a gummy. The dosage form picks the column. Where R&D leave the gummy column
blank — probiotics read "Strain Dependent" — the caps figure is used and the
substitution is stated on the quote, never passed off as theirs.

*Potency is per part **and claim basis**.* The asterisk in R&D's description
marks the moiety the label claims, so one part carries several potencies:
`L-Arginine HCl*` is 0.983 of the purchased salt, `L-Arginine* HCl` is 0.813.
Fourteen parts have more than one. No lookup can choose between them, so the
line is costed at potency 1.0 and the bases are named for a person to pick.

*Bulk density is measured, by lot.* 2,367 measurements across 571 parts. The
median is the working value; 46 parts vary by more than 1.5x between lots, and
where one is in a formula the fill check says so and gives the range.

*Capsule capacity is a grid, not a number.* R&D tabulate capacity by size
against blend density, and model tamping by rounding the measured density
**down** to a chart column and then advancing two columns — 0.50 g/mL is read
at 0.70. A fill that overflows loose but fits tamped is quoted with that said,
rather than refused.

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
./run.sh
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

### Curating the proposals

Nine hundred proposals is not nine hundred decisions. On the real catalogue
**30 parts carry 80% of purchasing spend**, so the worksheet is ranked by spend
with the cumulative share beside each row — fill in the shaded block at the top
and the rest can wait.

```bash
PYTHONPATH=backend .venv/bin/python -m quickquote.reference.curate export \
    --data-dir var/reference --out var/curation.xlsx
# R&D, Purchasing, Operations and Finance fill in the amber cells
PYTHONPATH=backend .venv/bin/python -m quickquote.reference.curate apply \
    --data-dir var/reference --worksheet var/curation.xlsx
curl -X POST http://localhost:8000/api/reference/reload
```

Four tabs, one per owner: **Ingredients** (overage class, potency, bulk
density), **Packaging** (role, bottles per purchased unit), **Machines** (run
rates), **Margins** (target margin).

Two properties make it safe to hand round:

- **A blank cell never clears a curated value**, and a cell left at the value
  the export pre-filled is not treated as an answer. Re-applying an untouched
  worksheet changes nothing.
- **A stated standardisation is offered, never applied.** `TURMERIC EXT. 95%`
  puts `0.95` in a *Suggested Potency* column with the text it came from. It
  never lands in the potency column, because a suggestion a person accepts is
  reference data and a suggestion applied silently is invented data.

### Reference tables

| File | Holds |
|---|---|
| `po_history.csv` | Part, description, UoM, latest/min/max cost, latest PO date, vendor, counts |
| `ingredient_identity.csv` | Canonical ingredient → aliases, overage class, per-material overage override, potency |
| `packaging_identity.csv` | Canonical packaging → aliases, role, whether size is required |
| `distinct_guard.csv` | Identity pairs that must never be matched |
| `overage.csv` | Overage % by class: multi- and single-ingredient, and the same again for gummies |
| `potency.csv` | Part code + claim basis → potency factor |
| `capsule_fill.csv` | Capsule size → mg capacity and shell volume |
| `capsule_capacity.csv` | Capsule size × blend density → mg capacity (R&D's chart) |
| `bulk_density_measured.csv` | Part code → median, min and max bulk density across received lots |
| `labor_rates.csv` | Rates, compounding bands, thresholds, defaults, volume ladder |
| `machines.csv` | Machine, work centre, labor/OH, run rate, capsule band, crew, set up, cleaning |
| `work_centres.csv` | Labor and OH per work centre, with crew size and set up time |
| `blenders.csv` | Blender volumes, for splitting a run into batches |
| `packaging_lines.csv` | Packaging line rates and crew by bottle count |
| `bulk_density.csv` | Bulk density by identity, then by class, for the fill check |
| `margin_rules.csv` | Minimum margin by account, on both cost bases |

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
tests/         356 tests
samples/       demo quote sheets and generated artifacts
```

---

## Known limits

- **Manufacturing covers compounding, encapsulation and bottling.** The
  operational price sheet also carries tariffs, freight, remnants, testing by
  ingredient count and customer-specific deductions; those are not modelled here.
- **Overage, potency, bulk density and capsule capacity come from R&D.**
  Their four working sheets are loaded by `ingest_rnd.py`. Two gaps remain
  in them: R&D record probiotic overage in a gummy as "Strain Dependent"
  rather than a number, and vitamin B12, omega oils and excipients have no
  row at all. Each is carried as a stated working value that flags itself,
  never as R&D's answer.
- **The reference data is not curated yet.** 676 ingredient and 214 packaging
  identities are marked PROPOSED, awaiting R&D and Purchasing. Picking an
  ingredient from the curated list quotes cleanly; typing a formula in plain
  English mostly comes back Unmatched or Needs Review until that review
  happens.
- **The shipped tables are demonstration data**, and they say so: every quote
  costed against them carries a blocking Finance flag, the workbook and PDF
  read NOT A QUOTE instead of INTERNAL DRAFT, and the web app shows a red
  banner. `scripts/load_real_data.sh` clears the marker when it rebuilds the
  tables from DrVita's exports.
- **Two of DrVita's own numbers disagree about the Schaefer.** Operations
  said 8,000 capsules an hour; the Curion Turmeric price sheet's machine table
  says 15,000. On a 60,000-capsule run that is 3.5 hours, or $0.36 a bottle.
  The engine uses Operations' figure.
- **Testing counts every formula line.** DrVita's sheets appear to count only
  the actives: a six-line formula with three excipients was quoted on the 1-3
  ingredient band. Finance to settle. The bands themselves are per batch and
  carry no per-unit charge — the two price sheets use one convention or the
  other, never both, and the Mar 2026 sheet's $0.45 a bottle is an alternative
  to a batch cost rather than an addition to it.
- **One rate Operations have not supplied.** The Chilsinator's granulation
  rate, which is the only thing still stopping a tablet being quoted. Every
  encapsulator, packaging line, powder and packet rate is confirmed.
- **Tablets and gummies cannot be quoted yet.** A tablet is blocked by
  granulation alone — Operations supplied the Fette press rate — and a gummy
  by having no work centre at all, which Finance confirmed in September 2026 is
  a cost model still being built. Capsules, powders and packets all quote. The
  *Process Steps* tab of the curation worksheet lists every gap in one place.
- **Which packaging line a product runs on is not known.** Operations gave
  rates for PKG1 and PKG2&3 but not which runs what, and the two differ by
  more than a third per bottle. Quotes assume PKG1 and say so, naming what
  PKG2&3 would have cost. Set `packaging_line` in `labor_rates.csv` to change
  the assumption.
- **Two of Operations' own numbers disagree about compounding.** The
  component-count bands charge 4.25–11.75 hours a batch; weighing, blending
  and dispensing as Operations timed them come to a third of that. The engine
  charges the bands and flags the gap rather than picking a side.
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
