"""Static configuration for the Quick Quote system."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
REFERENCE_DATA_DIR = Path(
    os.environ.get("QUICKQUOTE_REFERENCE_DIR", PACKAGE_ROOT / "reference" / "data")
)
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
