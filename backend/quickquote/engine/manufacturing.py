"""Labor, overhead and bottling estimate.

Single-batch production is assumed; multi-batch is out of scope for v1.
When volume is not provided the estimate is skipped entirely rather than
guessed.
"""
from __future__ import annotations

from ..reference.loader import ReferenceData
from ..schemas import ManufacturingEstimate, ProductSpec


def estimate_manufacturing(
    product: ProductSpec, component_count: int, reference: ReferenceData
) -> ManufacturingEstimate:
    """Compute per-bottle manufacturing cost, or explain why it was skipped."""
    machine = product.machine or reference.default_machine
    estimate = ManufacturingEstimate(
        component_count=component_count,
        machine=machine,
        machine_assumed=not product.machine,
    )

    bottles = product.annual_volume_bottles
    if not bottles or bottles <= 0:
        estimate.estimated = False
        estimate.reason = (
            "Volume not provided - manufacturing cost cannot be estimated because "
            "compounding and encapsulation are per-batch costs"
        )
        return estimate

    rate = reference.combined_rate
    estimate.bottles_in_run = bottles

    compounding_hours = reference.compounding_hours(component_count)
    estimate.compounding_hours = compounding_hours
    estimate.compounding_per_bottle = compounding_hours * rate / bottles

    is_capsule = "capsule" in (product.dosage_form or "capsule").lower()
    if is_capsule:
        encapsulation_hours = reference.rate("encapsulation_hours_per_batch", 8.67)
        estimate.encapsulation_hours = encapsulation_hours
        estimate.encapsulation_per_bottle = encapsulation_hours * rate / bottles
    else:
        estimate.encapsulation_hours = None
        estimate.encapsulation_per_bottle = 0.0

    bottles_per_hour = reference.rate("bottling_bottles_per_hour", 286)
    estimate.bottling_per_bottle = rate / bottles_per_hour if bottles_per_hour else 0.0

    estimate.total_per_bottle = (
        (estimate.compounding_per_bottle or 0.0)
        + (estimate.encapsulation_per_bottle or 0.0)
        + (estimate.bottling_per_bottle or 0.0)
    )
    estimate.estimated = True
    estimate.reason = "Single-batch production assumed"
    return estimate
