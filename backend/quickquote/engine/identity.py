"""Canonical identity resolution.

Identity is resolved by substring containment of a defined alias. Short
aliases need word boundaries. When several aliases hit, the longest alias
wins. Nothing here is fuzzy: a string either contains a defined alias or it
has no identity.

After every successful resolution the distinct-identity guard list is
checked. A description that reaches across a guarded pair -- ``Turmeric and
Garlic Powder Blend``, ``Glucosamine Sulfate / HCl`` -- is ambiguous by
definition, and an ambiguous line is never matched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..reference.loader import IdentityEntry, ReferenceData
from .text import alias_form, alias_matches_forms, spaced


@dataclass(frozen=True)
class Resolution:
    """A resolved canonical identity and the alias that produced it."""

    canonical: str
    alias: str
    entry: IdentityEntry
    all_matches: tuple[str, ...] = field(default=())

    @property
    def overage_class(self) -> str:
        return self.entry.overage_class

    @property
    def role(self) -> str:
        return self.entry.role


def resolve_identity(
    text: str | None, reference: ReferenceData, packaging: bool = False
) -> Resolution | None:
    """Resolve ``text`` to a canonical identity, or ``None``.

    The longest matching alias wins, so ``ashwagandha extract`` beats a
    shorter overlapping alias and ``glucosamine sulfate 2kcl`` beats
    ``glucosamine sulfate``.
    """
    text_spaced = spaced(text)
    if not text_spaced:
        return None
    text_tight = text_spaced.replace(" ", "")

    table = reference.packaging_identities if packaging else reference.ingredient_identities
    best: Resolution | None = None
    best_length = -1
    matched: list[str] = []

    for entry in table:
        entry_hit = False
        for alias in entry.aliases:
            form = alias_form(alias)
            if not alias_matches_forms(text_spaced, text_tight, form):
                continue
            entry_hit = True
            if len(form.tight) > best_length:
                best = Resolution(canonical=entry.canonical, alias=alias, entry=entry)
                best_length = len(form.tight)
        if entry_hit:
            matched.append(entry.canonical)

    if best is None:
        return None
    return Resolution(
        canonical=best.canonical,
        alias=best.alias,
        entry=best.entry,
        all_matches=tuple(dict.fromkeys(matched)),
    )


def guard_blocks(
    reference: ReferenceData, identity_a: str | None, identity_b: str | None
) -> str | None:
    """Return a guard reason when this identity pair must never be matched."""
    if not identity_a or not identity_b or identity_a == identity_b:
        return None
    if reference.is_guarded(identity_a, identity_b):
        return reference.guard_reason(identity_a, identity_b)
    return None


def guard_conflict(reference: ReferenceData, resolution: Resolution) -> str | None:
    """Return a reason when one description spans a guarded identity pair.

    Applied to every successful resolution. ``Garlic and Turmeric Powder``
    matches aliases on both sides of a guarded pair, so no single identity
    can be asserted and the line must not be matched.
    """
    identities = resolution.all_matches or (resolution.canonical,)
    for index, first in enumerate(identities):
        for second in identities[index + 1 :]:
            blocked = guard_blocks(reference, first, second)
            if blocked:
                return (
                    f"Description matches both '{first}' and '{second}', which the "
                    f"distinct-identity guard list forbids pairing - {blocked}"
                )
    return None
