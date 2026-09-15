"""Quote registry.

Quote results are held in memory for the life of the process and their
artifacts are written to disk, so a workbook or PDF can still be downloaded
after a restart.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

from .config import STORAGE_DIR
from .schemas import QuoteResult

MAX_IN_MEMORY = 200


class QuoteStore:
    """Registry of generated quote packages and their artifacts."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory or STORAGE_DIR)
        self._quotes: OrderedDict[str, QuoteResult] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory

    def add(self, result: QuoteResult) -> QuoteResult:
        with self._lock:
            self._quotes[result.quote_id] = result
            self._quotes.move_to_end(result.quote_id)
            while len(self._quotes) > MAX_IN_MEMORY:
                self._quotes.popitem(last=False)
        self._write_json(result)
        return result

    def get(self, quote_id: str) -> QuoteResult | None:
        with self._lock:
            return self._quotes.get(quote_id)

    def list(self, limit: int = 50) -> list[dict]:
        """Newest first, one summary row per quote."""
        with self._lock:
            results = list(self._quotes.values())
        results.sort(key=lambda result: result.created_at, reverse=True)
        return [
            {
                "quote_id": result.quote_id,
                "created_at": result.created_at.isoformat(),
                "customer": result.product.customer,
                "formula_name": result.product.formula_name,
                "source_name": result.parsed.source_name,
                "source_format": result.parsed.source_format,
                "primary_per_bottle": result.summary.primary_per_bottle,
                "low_per_bottle": result.summary.low_per_bottle,
                "high_per_bottle": result.summary.high_per_bottle,
                "quote_confidence": result.summary.quote_confidence,
                "unmatched_count": result.summary.unmatched_count,
                "needs_review_count": result.summary.needs_review_count,
                "flag_count": len(result.flags),
            }
            for result in results[:limit]
        ]

    # -- artifacts -----------------------------------------------------
    def artifact_path(self, quote_id: str, suffix: str) -> Path:
        return self.directory / f"{quote_id}{suffix}"

    def _write_json(self, result: QuoteResult) -> None:
        try:
            path = self.artifact_path(result.quote_id, ".json")
            path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        except OSError:
            # Artifact persistence is a convenience; never fail a quote for it.
            pass

    def write_artifact(self, quote_id: str, suffix: str, data: bytes) -> Path | None:
        try:
            path = self.artifact_path(quote_id, suffix)
            path.write_bytes(data)
            return path
        except OSError:
            return None

    def read_artifact(self, quote_id: str, suffix: str) -> bytes | None:
        path = self.artifact_path(quote_id, suffix)
        try:
            return path.read_bytes() if path.exists() else None
        except OSError:
            return None


_STORE: QuoteStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> QuoteStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = QuoteStore()
        return _STORE
