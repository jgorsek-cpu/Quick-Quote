"""Automatic derivation of fields a rep would otherwise have to work out.

Everything here fills a blank only. A value the rep typed is never
overwritten, and every value the system works out is recorded in
``ProductSpec.derived`` with the basis it was derived from, so the interface
can show what was assumed and the rep can override it.

Nothing here invents reference data. A bottle is chosen from the sizes that
actually exist in purchase-order history, and a machine from the run bands in
the machine table.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..reference.loader import Machine, PoRow, ReferenceData
from ..schemas import PackagingLine, ProductSpec
from .matching import po_rows_by_identity
from .text import extract_size, normalise_capsule_size


def _set(product: ProductSpec, field: str, value, basis: str) -> bool:
    """Fill a field only if the rep left it blank."""
    if getattr(product, field, None) not in (None, "", []):
        return False
    setattr(product, field, value)
    product.derived[field] = basis
    return True


# ------------------------------------------------------- serving maths

def derive_serving(product: ProductSpec) -> list[str]:
    """Complete the count / servings / capsules-per-serving triangle.

    ``count per bottle = capsules per serving x servings per bottle``. Given
    any two the third follows. A rep who types all three is left alone, even
    if they disagree -- that disagreement is reported, not corrected.
    """
    notes: list[str] = []
    count = product.count_per_bottle
    servings = product.servings_per_bottle
    per_serving = product.capsules_per_serving

    if per_serving is None and product.serving_size:
        from .text import extract_count

        stated = extract_count(product.serving_size)
        if stated:
            per_serving = stated
            _set(product, "capsules_per_serving", stated,
                 f"read from serving size '{product.serving_size}'")

    known = [value for value in (count, servings, per_serving) if value]
    if len(known) < 2:
        return notes

    if count and servings and not per_serving:
        if count % servings == 0:
            _set(product, "capsules_per_serving", count // servings,
                 f"{count} count / {servings} servings")
        else:
            notes.append(
                f"{count} count does not divide evenly into {servings} servings; "
                "capsules per serving left unset."
            )
    elif count and per_serving and not servings:
        if count % per_serving == 0:
            _set(product, "servings_per_bottle", count // per_serving,
                 f"{count} count / {per_serving} per serving")
        else:
            notes.append(
                f"{count} count does not divide evenly by {per_serving} per serving; "
                "servings per bottle left unset."
            )
    elif servings and per_serving and not count:
        _set(product, "count_per_bottle", servings * per_serving,
             f"{servings} servings x {per_serving} per serving")

    if product.count_per_bottle and product.servings_per_bottle and product.capsules_per_serving:
        expected = product.servings_per_bottle * product.capsules_per_serving
        if expected != product.count_per_bottle:
            notes.append(
                f"Count per bottle ({product.count_per_bottle}) does not equal "
                f"servings x capsules per serving ({expected}). Values are used "
                "as entered; confirm which is correct."
            )

    if not product.serving_size and product.capsules_per_serving:
        unit = "capsule" if product.capsules_per_serving == 1 else "capsules"
        _set(product, "serving_size", f"{product.capsules_per_serving} {unit}",
             f"{product.capsules_per_serving} capsules per serving")

    return notes


# ------------------------------------------------------ machine choice

def derive_machine(
    product: ProductSpec, reference: ReferenceData
) -> tuple[Machine | None, list[str]]:
    """Select the encapsulation work centre from the size of the run."""
    notes: list[str] = []

    named = reference.machine_by_name(product.machine)
    if named is not None:
        return named, notes

    if product.machine:
        notes.append(
            f"Machine '{product.machine}' is not in the machine table; "
            "its labor and overhead rates are not known."
        )
        return None, notes

    capsules = total_capsules(product)
    if capsules is None:
        return None, notes

    machine = reference.machine_for(capsules)
    if machine is not None:
        _set(product, "machine", machine.machine,
             f"{capsules:,.0f} capsules falls in this machine's run band")
    return machine, notes


def total_capsules(product: ProductSpec) -> float | None:
    if not product.count_per_bottle or not product.annual_volume_bottles:
        return None
    return float(product.count_per_bottle) * float(product.annual_volume_bottles)


# ------------------------------------------------- packaging selection

@dataclass
class BottleChoice:
    """A bottle picked from purchase-order history, and why."""

    row: PoRow
    volume_cc: float
    neck_mm: float | None
    required_cc: float
    basis: str


def required_bottle_cc(product: ProductSpec, reference: ReferenceData) -> float | None:
    """Volume a bottle must hold for this count of capsules.

    Capsules do not pack solid, so the shell volume is divided by the fill
    ratio the bottle reference carries.
    """
    capsule_ml = reference.capsule_volume_ml(normalise_capsule_size(product.capsule_size))
    if capsule_ml is None or not product.count_per_bottle:
        return None
    ratio = reference.bottle_fill_ratio or 0.80
    return product.count_per_bottle * capsule_ml / ratio


def choose_bottle(
    product: ProductSpec, reference: ReferenceData, canonical: str = "hdpe bottle white"
) -> BottleChoice | None:
    """Smallest stocked bottle that holds the fill.

    Bottles are only ever chosen from sizes with purchase-order history. The
    system does not size a bottle that has never been bought.
    """
    required = required_bottle_cc(product, reference)
    if required is None:
        return None

    candidates: list[BottleChoice] = []
    for row in po_rows_by_identity(reference, packaging=True).get(canonical, []):
        size = extract_size(row.description, role="bottle")
        if size.volume_cc is None or size.volume_cc < required:
            continue
        candidates.append(
            BottleChoice(
                row=row,
                volume_cc=size.volume_cc,
                neck_mm=size.neck_mm,
                required_cc=required,
                basis=(
                    f"{product.count_per_bottle} x size {product.capsule_size} capsules "
                    f"needs {required:,.0f}cc at a {reference.bottle_fill_ratio:.0%} fill ratio"
                ),
            )
        )

    if not candidates:
        return None
    return min(candidates, key=lambda choice: choice.volume_cc)


def choose_cap(
    reference: ReferenceData, neck_mm: float | None, canonical: str = "cr cap white"
) -> PoRow | None:
    """The closure matching a bottle's neck finish. Size is never substituted."""
    if neck_mm is None:
        return None
    for row in po_rows_by_identity(reference, packaging=True).get(canonical, []):
        if extract_size(row.description, role="cap").neck_mm == neck_mm:
            return row
    return None


def choose_capsule_shell(product: ProductSpec, reference: ReferenceData) -> PoRow | None:
    """The capsule shell matching the product's size and material exactly."""
    capsule_size = normalise_capsule_size(product.capsule_size)
    if capsule_size is None:
        return None
    material = (product.capsule_type or "vegetable").strip().lower()
    canonical = (
        "gelatin capsule shell" if material.startswith("gel") else "vegetable capsule shell"
    )
    for row in po_rows_by_identity(reference, packaging=True).get(canonical, []):
        if extract_size(row.description, role="capsule_shell").capsule_size == capsule_size:
            return row
    return None


# Roles filled in automatically, in the order a pack-out is built.
AUTO_ROLES: tuple[str, ...] = (
    "capsule_shell", "bottle", "cap", "label", "neckband", "cotton", "desiccant", "shipper",
)

# Roles with no size to work out: one per bottle of the only stocked option.
_SIMPLE_ROLES: dict[str, str] = {
    "label": "pressure sensitive label",
    "neckband": "shrink neckband",
    "cotton": "cotton coil",
    "desiccant": "desiccant canister",
    "shipper": "corrugated shipper",
}


def derive_packaging(
    product: ProductSpec,
    existing: list[PackagingLine],
    reference: ReferenceData,
    roles: tuple[str, ...] = AUTO_ROLES,
) -> tuple[list[PackagingLine], list[str]]:
    """Build a pack-out, leaving any component the rep already chose alone."""
    notes: list[str] = []
    present = {(line.role or "").strip().lower() for line in existing}
    added: list[PackagingLine] = []

    for role in roles:
        if role in present:
            continue

        if role == "capsule_shell":
            if "capsule" not in (product.dosage_form or "capsule").lower():
                continue
            row = choose_capsule_shell(product, reference)
            if row is None:
                notes.append(
                    "No capsule shell on file for size "
                    f"{product.capsule_size or '(unset)'} in "
                    f"{product.capsule_type or 'vegetable'}; add it manually."
                )
                continue
            added.append(PackagingLine(role=role, description=row.description,
                                       notes="Size and material matched to the product spec"))
            continue

        if role == "bottle":
            choice = choose_bottle(product, reference)
            if choice is None:
                notes.append(
                    "No stocked bottle is large enough, or the capsule size and count "
                    "are not both set, so no bottle was chosen."
                )
                continue
            product.derived["bottle"] = choice.basis
            added.append(PackagingLine(role=role, description=choice.row.description,
                                       notes=choice.basis))
            continue

        if role == "cap":
            bottle_line = next(
                (line for line in [*existing, *added] if line.role == "bottle"), None
            )
            neck = extract_size(bottle_line.description, role="bottle").neck_mm if bottle_line else None
            row = choose_cap(reference, neck)
            if row is None:
                if neck is None:
                    notes.append("Bottle neck finish is unknown, so no closure was chosen.")
                else:
                    notes.append(f"No stocked closure matches a {neck:g}mm neck finish.")
                continue
            added.append(PackagingLine(role=role, description=row.description,
                                       notes=f"Matched to the bottle's {neck:g}mm neck finish"))
            continue

        canonical = _SIMPLE_ROLES.get(role)
        if canonical is None:
            continue
        entry = reference.identity_entry(canonical, packaging=True)
        if entry is None:
            continue
        options = po_rows_by_identity(reference, packaging=True).get(canonical, [])
        if len(options) != 1:
            if not options:
                notes.append(f"No purchase-order record for {canonical}; not added.")
            else:
                notes.append(
                    f"{len(options)} records share identity '{canonical}'; "
                    "choose one rather than letting the system pick."
                )
            continue
        added.append(PackagingLine(role=role, description=options[0].description))

    return added, notes
