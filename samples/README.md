# Sample quote sheets

Two demonstration inputs, both parsed by the same document pipeline:

- `vitamin_shoppe_quote_request.xlsx` — a spreadsheet quote request. Reproduces
  the worked example in the system specification, including its two unmatched
  ingredients and its review flags.
- `acme_quote_request.txt` — the same information as plain text, with a
  delimited ingredient block and `Bottle: …` style packaging declarations.

Generate the workbook and PDF for either one:

```bash
PYTHONPATH=backend .venv/bin/python - <<'PY'
from datetime import date
from quickquote.parsing.router import parse_document
from quickquote.engine.pipeline import run_pipeline
from quickquote.reference.loader import get_reference_data
from quickquote.output.workbook import build_workbook
from quickquote.output.pdf import build_quote_pdf

source = "samples/vitamin_shoppe_quote_request.xlsx"
parsed = parse_document(open(source, "rb").read(), source)
result = run_pipeline(parsed, get_reference_data(), as_of=date.today())
open("samples/quote_package_demo.xlsx", "wb").write(build_workbook(result))
open("samples/quote_summary_demo.pdf", "wb").write(build_quote_pdf(result))
print(f"${result.summary.primary_per_bottle:.2f}/bottle, "
      f"{result.summary.quote_confidence} confidence, {len(result.flags)} flags")
PY
```

Or upload either file through the web app's **Upload a quote sheet** tab.
