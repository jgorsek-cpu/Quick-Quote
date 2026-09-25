"""Static configuration for the Quick Quote system."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def _reference_data_dir() -> Path:
    """Where the reference tables live.

    An explicit QUICKQUOTE_REFERENCE_DIR always wins. Otherwise the
    operational tables are used when they have been built, because forgetting
    to point at them is the one mistake that produces a complete, confident
    quote out of demonstration prices. The shipped tables are the fallback,
    and they announce themselves as a demonstration when they are used.
    """
    override = os.environ.get("QUICKQUOTE_REFERENCE_DIR")
    if override:
        return Path(override)
    operational = PROJECT_ROOT / "var" / "reference"
    if (operational / "po_history.csv").exists():
        return operational
    return PACKAGE_ROOT / "reference" / "data"


REFERENCE_DATA_DIR = _reference_data_dir()
FRONTEND_DIR = Path(
    os.environ.get("QUICKQUOTE_FRONTEND_DIR", PROJECT_ROOT / "frontend")
)
STORAGE_DIR = Path(
    os.environ.get("QUICKQUOTE_STORAGE_DIR", PROJECT_ROOT / "var" / "quotes")
)

MAX_UPLOAD_BYTES = int(os.environ.get("QUICKQUOTE_MAX_UPLOAD_BYTES", 15 * 1024 * 1024))
SUPPORTED_UPLOAD_SUFFIXES = {".xlsx", ".xlsm", ".pdf", ".docx", ".txt", ".csv"}

# Flag owners are fixed by the specification and must never be renamed,
# reordered or collapsed.
FLAG_OWNERS = ("Purchasing", "R&D", "Operations", "Sales", "Finance")

# Match statuses. Only ACCEPTED lines contribute to primary quote cost.
ACCEPTED = "Accepted"
NEEDS_REVIEW = "Needs Review"
UNMATCHED = "Unmatched"

HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"

EXCLUDED_DISPLAY = "Excluded"
