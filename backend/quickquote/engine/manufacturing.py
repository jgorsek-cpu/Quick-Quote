"""Labor, overhead and bottling estimate.

Each production step is costed at its own work centre's rate rather than one
blended rate: compounding at the blend rate, encapsulation at the selected
machine's rate, bottling at the packaging line's rate. Encapsulation hours
come from the machine's run rate and the number of capsules in the run.

Single-batch production is assumed; multi-batch is out of scope for v1. When
volume is not provided the estimate is skipped entirely rather than guessed.
"""
from __future__ import annotations

from ..reference.loader import ReferenceData
from ..schemas import ManufacturingEstimate, ProductSpec
from .derive import derive_machine, total_capsules


def estimate_manufacturing(
    product: ProductSpec, component_count: int, reference: ReferenceData
) -> tuple[ManufacturingEstimate, list[str]]:
    """Compute per-bottle manufacturing cost, or explain why it was skipped."""
    machine, notes = derive_machine(product, reference)

    estimate = ManufacturingEstimate(
        component_count=component_count,
        machine=machine.machine if machine else (product.machine or reference.default_machine),
        bottling_line=reference.text_rate("bottling_line", "CVC1"),
        machine_assumed=bool(product.derived.get("machine")) or not product.machine,
    )
    if machine is not None and product.derived.get("machine"):
        estimate.machine_basis = product.derived["machine"]
    elif product.machine:
        estimate.machine_basis = "Specified on the quote"
    else:
        estimate.machine_basis = "Default assumption - run size is unknown"

    bottles = product.annual_volume_bottles
    if not bottles or bottles <= 0:
        estimate.estimated = False
        estimate.reason = (
            "Volume not provided - manufacturing cost cannot be estimated because "
            "compounding and encapsulation are per-batch costs"
        )
        return estimate, notes

    estimate.bottles_in_run = bottles
    estimate.total_capsules = total_capsules(product)

    compounding_rate = reference.step_rate("compounding")
    bottling_rate = reference.step_rate("bottling")
    estimate.compounding_rate = compounding_rate
    estimate.bottling_rate = bottling_rate

    compounding_hours = reference.compounding_hours(component_count)
    estimate.compounding_hours = compounding_hours
    estimate.compounding_per_bottle = compounding_hours * compounding_rate / bottles

    is_capsule = "capsule" in (product.dosage_form or "capsule").lower()
    if is_capsule:
        if machine is not None and reference.use_work_centre_rates:
            encapsulation_rate = machine.combined_rate
            capsules = estimate.total_capsules
            if capsules:
                encapsulation_hours = capsules / machine.capsules_per_hour
            else:
                encapsulation_hours = reference.rate("encapsulation_hours_per_batch", 8.67)
        else:
            encapsulation_rate = reference.combined_rate
            encapsulation_hours = reference.rate("encapsulation_hours_per_batch", 8.67)
        estimate.encapsulation_rate = encapsulation_rate
        estimate.encapsulation_hours = encapsulation_hours
        estimate.encapsulation_per_bottle = encapsulation_hours * encapsulation_rate / bottles
    else:
        estimate.encapsulation_hours = None
        estimate.encapsulation_per_bottle = 0.0
        estimate.encapsulation_rate = None

    bottles_per_hour = reference.rate("bottling_bottles_per_hour", 286)
    estimate.bottling_per_bottle = bottling_rate / bottles_per_hour if bottles_per_hour else 0.0

    estimate.total_per_bottle = (
        (estimate.compounding_per_bottle or 0.0)
        + (estimate.encapsulation_per_bottle or 0.0)
        + (estimate.bottling_per_bottle or 0.0)
    )
    estimate.estimated = True
    estimate.reason = "Single-batch production assumed"
    return estimate, notes
