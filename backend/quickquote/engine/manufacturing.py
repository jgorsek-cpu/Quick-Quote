"""Manufacturing cost, walked step by step along the dosage form's route.

The route comes from reference data, not from code: capsule, tablet, powder,
packet, softgel and gummy each list their own steps, and each step names the
work centre that runs it. Adding a line is a row in ``process_routes.csv``.

Every step is costed at its own work centre's rate. A step whose rate or
throughput is not on file is **not estimated and not silently dropped** -- it
is recorded, reported to Operations, and the total is marked incomplete, so a
tablet quote cannot quietly come back priced as though pressing were free.

A run is split into batches by blender capacity. Compounding, set up and
cleaning are paid once per batch, so a run too large for one batch pays them
again for every batch it needs; run time on a machine scales with the order
either way.

Labor is per operator. Packaging runs four or five of them, so an hour on that
line costs four or five times its labor rate; overhead is per machine hour and
is not multiplied.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..reference.loader import PackagingLine, ReferenceData, RouteStep
from ..schemas import ManufacturingEstimate, ManufacturingStep, ProductSpec
from .derive import derive_machine, total_capsules


@dataclass
class _Context:
    """What the route needs to know about the run."""

    bottles: int
    units: float | None          # capsules, tablets, packets in the whole run
    component_count: int
    count_per_bottle: int | None
    capsule_size: str | None
    batches: int = 1
    packaging_line: "PackagingLine | None" = None


def estimate_manufacturing(
    product: ProductSpec,
    component_count: int,
    reference: ReferenceData,
    blend_kg_per_bottle: float | None = None,
    blend_density_g_ml: float | None = None,
) -> tuple[ManufacturingEstimate, list[str]]:
    """Cost every step of this dosage form's route, or say why it could not."""
    machine, notes = derive_machine(product, reference)

    dosage_form = (product.dosage_form or "capsule").strip()
    route = reference.route_for(dosage_form)

    estimate = ManufacturingEstimate(
        component_count=component_count,
        dosage_form=dosage_form,
        machine=machine.machine if machine else (product.machine or ""),
        bottling_line=reference.text_rate("bottling_line", "CVC1"),
        machine_assumed=bool(product.derived.get("machine")) or not product.machine,
    )
    if machine is not None and product.derived.get("machine"):
        estimate.machine_basis = product.derived["machine"]
    elif product.machine:
        estimate.machine_basis = "Specified on the quote"
    else:
        estimate.machine_basis = "Run size unknown"

    if not route:
        estimate.estimated = False
        estimate.reason = (
            f"No process route on file for dosage form '{dosage_form}'. "
            f"Known forms: {', '.join(reference.known_dosage_forms())}."
        )
        return estimate, notes

    bottles = product.annual_volume_bottles
    if not bottles or bottles <= 0:
        estimate.estimated = False
        estimate.reason = (
            "Volume not provided - manufacturing cost cannot be estimated because "
            "compounding and machine time are per-batch costs"
        )
        return estimate, notes

    estimate.bottles_in_run = bottles
    estimate.total_capsules = total_capsules(product)

    # How many batches the run needs. Blender capacity is a volume limit, so
    # the weight it holds depends on the blend's density: where every material
    # has a density on file the blend's own is used, otherwise Operations'
    # quoting assumption of 0.4 g/mL, which gives the smaller batch.
    blend_kg = (blend_kg_per_bottle or 0.0) * bottles or None
    batches, blender, density = reference.batches_for(blend_kg, blend_density_g_ml)
    estimate.batches = batches
    estimate.blender = blender.blender if blender else ""
    estimate.blend_kg = blend_kg
    estimate.blend_density_g_ml = density
    estimate.blend_density_assumed = blend_density_g_ml is None

    line_row = reference.packaging_line_row(product.count_per_bottle)
    estimate.packaging_line = line_row.line if line_row else ""
    estimate.packaging_line_assumed = True
    estimate.bottling_line = estimate.packaging_line or estimate.bottling_line

    context = _Context(
        bottles=bottles,
        units=estimate.total_capsules,
        component_count=component_count,
        count_per_bottle=product.count_per_bottle,
        capsule_size=product.capsule_size,
        batches=batches,
        packaging_line=line_row,
    )

    for step in route:
        estimate.steps.append(_cost_step(step, context, reference, machine))

    costed = [step for step in estimate.steps if step.cost_per_bottle is not None]
    estimate.total_per_bottle = sum(step.cost_per_bottle or 0.0 for step in costed)
    estimate.uncosted_steps = [
        step.step for step in estimate.steps if step.cost_per_bottle is None
    ]
    estimate.uncosted_critical_steps = [
        route_step.step
        for route_step, step in zip(route, estimate.steps)
        if route_step.critical and step.cost_per_bottle is None
    ]

    # Keep the named figures the rest of the system reads.
    for step in estimate.steps:
        name = step.step.lower()
        if "compounding" in name:
            estimate.compounding_per_bottle = step.cost_per_bottle
            estimate.compounding_hours = step.hours
            estimate.compounding_rate = step.rate_per_hour
        elif "encapsulation" in name and "cleaning" not in name:
            estimate.encapsulation_per_bottle = step.cost_per_bottle
            estimate.encapsulation_hours = step.hours
            estimate.encapsulation_rate = step.rate_per_hour
        elif "bottling" in name:
            estimate.bottling_per_bottle = step.cost_per_bottle
            estimate.bottling_rate = step.rate_per_hour

    # A core step that cannot be costed invalidates the estimate: a tablet
    # quote must not come back priced as though pressing were free. Cleaning is
    # real cost but ancillary, so its absence understates rather than invalidates.
    estimate.estimated = bool(costed) and not estimate.uncosted_critical_steps

    if estimate.uncosted_critical_steps:
        missing = ", ".join(estimate.uncosted_critical_steps)
        estimate.reason = (
            f"Manufacturing not estimated for a {dosage_form}: the "
            f"{'step that defines' if len(estimate.uncosted_critical_steps) == 1 else 'steps that define'} "
            f"the process could not be costed ({missing}). "
            "Operations must supply the missing rates before this form can be quoted."
        )
    elif estimate.uncosted_steps:
        estimate.reason = (
            f"{_batch_phrase(estimate)}. {len(estimate.uncosted_steps)} "
            f"ancillary step(s) could not be costed, so this total is understated: "
            + ", ".join(estimate.uncosted_steps)
        )
    else:
        estimate.reason = _batch_phrase(estimate)

    return estimate, notes


def _batch_phrase(estimate: ManufacturingEstimate) -> str:
    """How the run was split, and on what."""
    if not estimate.blender:
        return (
            "Single batch assumed - blend weight unknown" if not estimate.blend_kg
            else "Single batch assumed - no blender capacity on file"
        )
    plural = "" if estimate.batches == 1 else "es"
    basis = "assumed" if estimate.blend_density_assumed else "from the formula"
    return (
        f"{estimate.batches} batch{plural} on the {estimate.blender} blender "
        f"({estimate.blend_kg:,.0f} kg of blend at {estimate.blend_density_g_ml:g} g/mL, "
        f"{basis})" if estimate.blend_kg else
        f"Single batch on the {estimate.blender} blender - blend weight unknown"
    )


def _cost_step(
    step: RouteStep, context: _Context, reference: ReferenceData, machine
) -> ManufacturingStep:
    """Cost one route step, or record precisely what is missing."""
    result = ManufacturingStep(step=step.step, work_centre=step.work_centre, basis=step.basis)

    # Encapsulation's work centre is the machine the run size selected.
    work_centre = step.work_centre
    if step.basis == "units_per_hour_machine":
        if machine is None:
            result.reason = (
                "No machine could be selected for this run, so encapsulation "
                "time is unknown."
            )
            return result
        work_centre = machine.work_centre
        result.work_centre = machine.machine

    # The packaging line decides its own crew: the same centre runs four
    # operators on PKG1 and five on PKG2&3.
    crew = None
    if step.basis == "bottles_per_hour_table" and context.packaging_line is not None:
        crew = context.packaging_line.crew_size

    rate = reference.centre_rate(work_centre, crew)
    if rate is None:
        result.reason = (
            f"No labor and overhead rate on file for "
            f"{work_centre or 'this step'} - Operations to supply."
        )
        return result
    result.rate_per_hour = rate
    centre = reference.work_centres.get(work_centre)
    result.crew_size = crew if crew is not None else (centre.crew_size if centre else None)

    hours, per_batch, reason = _hours_for(step, context, reference, machine, work_centre)
    if hours is None:
        result.reason = reason
        return result

    # Set up is paid once per batch, like the batch work itself.
    setup = _setup_hours(step, context, reference, machine, work_centre)
    run_hours = hours * context.batches if per_batch else hours

    result.per_batch = per_batch
    result.run_hours = run_hours
    result.setup_hours = setup * context.batches if setup else None
    result.hours = run_hours + (result.setup_hours or 0.0)
    result.cost_per_bottle = result.hours * rate / context.bottles
    return result


def _setup_hours(
    step: RouteStep, context: _Context, reference: ReferenceData, machine, work_centre: str
) -> float | None:
    """Set up time for one batch at this step, or ``None`` when not on file.

    Cleaning steps carry no set up of their own -- the cleaning time *is* the
    step -- and an unknown set up understates rather than blocks, because a
    step that runs is not made unquotable by one missing figure.
    """
    if step.basis == "fixed_hours":
        return None
    if step.basis == "bottles_per_hour_table" and context.packaging_line is not None:
        return context.packaging_line.setup_hours
    if step.basis == "units_per_hour_machine" and machine is not None:
        return machine.setup_hours
    centre = reference.work_centres.get(work_centre)
    return centre.setup_hours if centre else None


def _hours_for(
    step: RouteStep, context: _Context, reference: ReferenceData, machine, work_centre: str
) -> tuple[float | None, bool, str]:
    """``(hours, per_batch, reason)`` for this step.

    ``per_batch`` says whether the hours are paid once per batch -- compounding
    and cleaning are -- or already cover the whole run, as machine time does.
    """
    if step.basis == "batch_hours_by_components":
        return reference.compounding_hours(context.component_count), True, ""

    if step.basis == "fixed_hours":
        # Encapsulation cleaning depends on the machine: two hours on the 705,
        # six on the 3005, so it is read from the machine the run selected.
        hours = None
        if machine is not None and "encap" in work_centre.lower():
            hours = machine.cleaning_hours
        if hours is None:
            hours = reference.cleaning_hours.get(work_centre)
        if hours is None and context.packaging_line is not None \
                and "packaging" in work_centre.lower():
            hours = context.packaging_line.cleaning_hours
        if hours is None:
            return None, True, (
                f"Cleaning time for {work_centre} is not on file - "
                "Operations to supply."
            )
        return hours, True, ""

    if step.basis in ("units_per_hour", "units_per_hour_machine"):
        if step.basis == "units_per_hour_machine" and machine is not None:
            speed, unit = machine.capsules_per_hour, "capsules"
        else:
            rate_row = reference.run_rates.get(work_centre)
            speed = rate_row.units_per_hour if rate_row else None
            unit = (rate_row.unit if rate_row else "") or "units"
        if not speed:
            return None, False, (
                f"Run rate for {work_centre} is not on file - "
                "Operations to supply units per hour."
            )
        # A line rated in bottles per hour fills bottles, not capsules: the
        # powder line runs 1,200 bottles an hour whatever goes in them.
        quantity = context.bottles if unit.strip().lower() == "bottles" else context.units
        if not quantity:
            return None, False, (
                "Units in the run are unknown: count per bottle and volume are "
                "both needed."
            )
        return quantity / speed, False, ""

    if step.basis == "bottles_per_hour_table":
        speed = None
        if context.packaging_line is not None:
            speed = context.packaging_line.bottles_per_hour
        if not speed:
            speed = reference.bottles_per_hour(context.count_per_bottle, context.capsule_size)
        if not speed:
            speed = reference.rate("bottling_bottles_per_hour", 0) or None
        if not speed:
            return None, False, "Bottling line speed is not on file for this count."
        return context.bottles / speed, False, ""

    return None, False, f"Unknown basis '{step.basis}' for this step."


def testing_cost_per_bottle(
    reference: ReferenceData, ingredient_count: int, bottles: int | None
) -> tuple[float | None, str]:
    """Analytical testing cost, from the band for this ingredient count."""
    band = reference.testing_for(ingredient_count)
    if band is None or not bottles:
        return None, ""
    per_bottle = band.per_unit_cost + (band.batch_cost / bottles)
    noun = "ingredient" if ingredient_count == 1 else "ingredients"
    return per_bottle, (
        f"{ingredient_count} {noun}: ${band.batch_cost:,.0f} per batch plus "
        f"${band.per_unit_cost:,.2f} per unit"
    )
