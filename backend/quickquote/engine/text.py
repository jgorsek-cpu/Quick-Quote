"""Deterministic text normalisation and size extraction.

Two normal forms are produced for every string:

``spaced``
    lower-cased, every non-alphanumeric run collapsed to a single space.
    Word boundaries survive, so short tokens can be matched safely.

``tight``
    ``spaced`` with all spaces removed. Lets ``coenzyme q-10`` meet
    ``coenzyme q10``. Only long aliases may use this form, which is what
    stops ``msm`` colliding with ``mm smooth silver``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

SHORT_ALIAS_LEN = 5

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")

_CAPSULE_SIZE_RE = re.compile(r"\bsize\s*(000|00|0|1|2|3|4)\b")
_BARE_CAPSULE_SIZE_RE = re.compile(r"\b(000|00)\b")
_VOLUME_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(cc|ml)\b")
_NECK_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*mm\b")
# Packaging quotes a closure as a neck finish: "45/400" is a 45mm neck with a
# 400 thread. Normalisation has already turned the slash into a space by the
# time this runs. Only real thread codes are accepted, so an incidental pair of
# numbers such as "50 50" in "50/50 SIL/CARB" is not read as a neck size.
_THREAD_CODES = "400|410|415|425|430|445|450|460|470|480|485|490"
_NECK_FINISH_RE = re.compile(
    r"\b(\d{2,3})\s*(?:mm)?\s+(?:" + _THREAD_CODES + r")\b"
)
_COUNT_RE = re.compile(r"\b(\d+)\s*(?:ct|count|caps?|capsules?|tablets?|tabs?)\b")


def spaced(text: str | None) -> str:
    """Lower-case, punctuation-to-space, whitespace-collapsed form."""
    if not text:
        return ""
    return _WS.sub(" ", _NON_ALNUM.sub(" ", str(text).lower())).strip()


def tight(text: str | None) -> str:
    """``spaced`` with spaces removed."""
    return spaced(text).replace(" ", "")


def contains_word(haystack_spaced: str, needle_spaced: str) -> bool:
    """True when ``needle`` appears in ``haystack`` on whole-word boundaries."""
    if not needle_spaced or not haystack_spaced:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(needle_spaced) + r"(?![a-z0-9])"
    return re.search(pattern, haystack_spaced) is not None


@dataclass(frozen=True)
class AliasForm:
    """An alias with its normal forms and boundary pattern worked out once.

    Normalising an alias and compiling its word-boundary pattern are pure
    functions of the alias, but the matcher used to redo both for every
    candidate string. Against a real catalogue that was a million regex
    compilations per index build.
    """

    spaced: str
    tight: str
    short: bool
    pattern: re.Pattern


@lru_cache(maxsize=16384)
def alias_form(alias: str) -> AliasForm | None:
    """The cached normal forms for one alias."""
    alias_spaced = spaced(alias)
    if not alias_spaced:
        return None
    alias_tight = alias_spaced.replace(" ", "")
    return AliasForm(
        spaced=alias_spaced,
        tight=alias_tight,
        short=len(alias_tight) < SHORT_ALIAS_LEN,
        pattern=re.compile(
            r"(?<![a-z0-9])" + re.escape(alias_spaced) + r"(?![a-z0-9])"
        ),
    )


def alias_matches_forms(text_spaced: str, text_tight: str, form: AliasForm | None) -> bool:
    """Apply the containment rules against already-normalised text.

    Short aliases must land on word boundaries and may never match through the
    compact form, which is what stops ``msm`` colliding with
    ``mm smooth silver``.
    """
    if form is None or not text_spaced:
        return False
    if form.short:
        return form.pattern.search(text_spaced) is not None
    if form.pattern.search(text_spaced) is not None:
        return True
    if form.spaced in text_spaced:
        return True
    return form.tight in text_tight


def alias_matches(text: str, alias: str) -> bool:
    """Apply the containment rules for one alias."""
    text_spaced = spaced(text)
    return alias_matches_forms(text_spaced, text_spaced.replace(" ", ""), alias_form(alias))


@dataclass(frozen=True)
class SizeSignature:
    """The size facts a packaging description asserts."""

    capsule_size: str | None = None
    volume_cc: float | None = None
    neck_mm: float | None = None

    @property
    def empty(self) -> bool:
        return self.capsule_size is None and self.volume_cc is None and self.neck_mm is None

    def describe(self) -> str:
        parts = []
        if self.capsule_size:
            parts.append(f"size {self.capsule_size}")
        if self.volume_cc is not None:
            parts.append(f"{self.volume_cc:g}cc")
        if self.neck_mm is not None:
            parts.append(f"{self.neck_mm:g}mm")
        return ", ".join(parts) or "no size stated"

    def conflicts_with(self, other: "SizeSignature") -> bool:
        """True when both sides state a size and the sizes differ.

        A dimension either side leaves unstated is not a conflict; a dimension
        both sides state must be identical. 175cc never matches 250cc.
        """
        for mine, theirs in (
            (self.capsule_size, other.capsule_size),
            (self.volume_cc, other.volume_cc),
            (self.neck_mm, other.neck_mm),
        ):
            if mine is not None and theirs is not None and mine != theirs:
                return True
        return False

    def covers(self, other: "SizeSignature") -> bool:
        """True when every size dimension ``other`` states is matched here."""
        if other.empty:
            return False
        if other.capsule_size is not None and self.capsule_size != other.capsule_size:
            return False
        if other.volume_cc is not None and self.volume_cc != other.volume_cc:
            return False
        if other.neck_mm is not None and self.neck_mm != other.neck_mm:
            return False
        return True


def extract_size(text: str | None, role: str = "") -> SizeSignature:
    """Pull capsule size, bottle volume and neck finish out of a description."""
    normalised = spaced(text)
    if not normalised:
        return SizeSignature()

    capsule_size: str | None = None
    if role in ("", "capsule_shell") or "capsule" in normalised:
        match = _CAPSULE_SIZE_RE.search(normalised)
        if match:
            capsule_size = match.group(1)
        elif "capsule" in normalised:
            bare = _BARE_CAPSULE_SIZE_RE.search(normalised)
            if bare:
                capsule_size = bare.group(1)

    volume_cc: float | None = None
    volume_match = _VOLUME_RE.search(normalised)
    if volume_match:
        volume_cc = float(volume_match.group(1))

    neck_mm: float | None = None
    neck_match = _NECK_RE.search(normalised)
    if neck_match:
        neck_mm = float(neck_match.group(1))
    else:
        finish_match = _NECK_FINISH_RE.search(normalised)
        if finish_match:
            neck_mm = float(finish_match.group(1))

    return SizeSignature(capsule_size=capsule_size, volume_cc=volume_cc, neck_mm=neck_mm)


def normalise_capsule_size(value: str | None) -> str | None:
    """Coerce a capsule size written any of the usual ways to its token."""
    if value is None:
        return None
    text = spaced(value)
    if not text:
        return None
    match = _CAPSULE_SIZE_RE.search(text) or _CAPSULE_SIZE_RE.search(f"size {text}")
    if match:
        return match.group(1)
    if text in {"000", "00", "0", "1", "2", "3", "4"}:
        return text
    return None


def extract_count(text: str | None) -> int | None:
    """Pull a bottle count such as ``90ct`` out of free text."""
    match = _COUNT_RE.search(spaced(text))
    return int(match.group(1)) if match else None
