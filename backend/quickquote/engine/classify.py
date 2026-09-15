"""Potency and overage-class determination.

Neither value is ever invented or extrapolated. Each is looked up, or
defaulted with a flag that names the default that was applied.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..reference.loader import ReferenceData
from .identity import Resolution
from .text import alias_matches, spaced

# Ordered name-based fallback used only when a part code and a resolved
# identity both fail to supply an overage class. First match wins.
_NAME_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("vitamin a", "retinyl", "beta carotene"), "vitamin_a"),
    (("biotin",), "biotin"),
    (("vitamin b12", "cobalamin"), "vitamin_b12"),
    (("folic acid", "folate", "methylfolate"), "folate"),
    (("vitamin d", "cholecalciferol"), "vitamin_d"),
    (("vitamin e", "tocopher"), "vitamin_e"),
    (("vitamin k", "menaquinone"), "vitamin_k"),
    (("vitamin c", "ascorbic"), "vitamin_c"),
    (("thiamine", "riboflavin", "niacin", "pyridoxine", "pantothenic", "vitamin b"), "vitamin_b6"),
    (("coq10", "coenzyme q", "ubiquin"), "coq10"),
    (("lutein", "zeaxanthin", "astaxanthin"), "lutein"),
    (("probiotic", "lactobacillus", "bifidobacter", "cfu"), "probiotic"),
    (("bromelain", "papain", "protease", "amylase", "lipase", "enzyme"), "enzyme"),
    (("fish oil", "epa", "dha", "omega 3", "krill", "flax oil"), "omega_oil"),
    (("calcium", "magnesium"), "calcium_magnesium"),
    (("iron", "ferrous"), "iron"),
    (("zinc", "copper", "manganese", "selenium", "chromium", "iodine", "molybdenum"), "trace_minerals"),
    (("amino acid", "arginine", "citrulline", "theanine", "creatine", "glycine",
      "taurine", "carnitine", "glutamine", "lysine", "tyrosine"), "amino_acids"),
    (("extract", "standardized", "concentrate"), "botanical_extract"),
    (("powder", "root", "leaf", "herb", "bark", "fruit"), "botanical_powder"),
    (("stearate", "cellulose", "silica", "silicon dioxide", "maltodextrin", "rice flour"), "excipient"),
)


@dataclass
class Classification:
    """The potency and overage decided for one ingredient line."""

    potency: float
    potency_source: str
    potency_defaulted: bool
    overage_class: str
    overage_pct: float
    overage_defaulted: bool
    overage_source: str = ""


def classify_by_name(name: str | None) -> str | None:
    """Fallback overage class from the ingredient name, or ``None``."""
    text = spaced(name)
    if not text:
        return None
    for needles, overage_class in _NAME_RULES:
        for needle in needles:
            if alias_matches(name, needle):
                return overage_class
    return None


def classify_ingredient(
    name: str,
    reference: ReferenceData,
    resolution: Resolution | None,
    input_part_code: str | None,
    matched_code: str | None,
    multi_ingredient: bool,
) -> Classification:
    """Decide potency and overage for one ingredient line."""

    # -- potency: part code, then identity default, then 1.0 + flag ---
    potency = reference.potency_for_part(input_part_code)
    potency_source = f"Potency table (part {input_part_code})" if potency else ""
    if potency is None:
        potency = reference.potency_for_part(matched_code)
        if potency:
            potency_source = f"Potency table (part {matched_code})"
    if potency is None and resolution is not None:
        entry_potency = resolution.entry.default_potency
        if entry_potency:
            potency = entry_potency
            potency_source = f"Identity default for '{resolution.canonical}'"
    potency_defaulted = potency is None
    if potency_defaulted:
        potency = reference.rate("default_potency", 1.0)
        potency_source = "Default 1.0 applied - potency unknown"

    # -- overage: identity class, then name rules, then default + flag -
    overage_class = resolution.overage_class if resolution is not None else None
    class_source_defaulted = False
    if not overage_class or overage_class == "default":
        overage_class = classify_by_name(name) or "default"
    if overage_class == "default":
        class_source_defaulted = True

    # A material-specific overage wins over its class, so an unusual material
    # can be set without moving the whole group it belongs to.
    override = resolution.entry.overage_pct if resolution is not None else None
    if override is not None:
        return Classification(
            potency=potency,
            potency_source=potency_source,
            potency_defaulted=potency_defaulted,
            overage_class=overage_class,
            overage_pct=override / 100.0,
            overage_defaulted=False,
            overage_source=f"Material-specific overage for '{resolution.canonical}'",
        )

    overage_pct = reference.overage_pct(overage_class, multi_ingredient)
    if overage_pct is None:
        overage_class = "default"
        class_source_defaulted = True
        overage_pct = reference.overage_pct("default", multi_ingredient) or 0.05

    return Classification(
        potency=potency,
        potency_source=potency_source,
        potency_defaulted=potency_defaulted,
        overage_class=overage_class,
        overage_pct=overage_pct,
        overage_defaulted=class_source_defaulted,
        overage_source=(
            "Reference default - class could not be determined"
            if class_source_defaulted else f"Overage class '{overage_class}'"
        ),
    )
