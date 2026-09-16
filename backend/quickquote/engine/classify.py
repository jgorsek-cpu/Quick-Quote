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
    # Vitamins. R&D price each B vitamin separately -- thiamine is 30/10 where
    # B6 is 30/20 -- so they are not collapsed into one group. A plain
    # "B complex" matches none of these and is flagged rather than averaged.
    (("vitamin a", "retinyl", "retinol"), "vitamin_a"),
    (("beta carotene", "betacarotene", "carotenoid"), "beta_carotene"),
    (("thiamine", "thiamin", "vitamin b1"), "thiamine"),
    (("riboflavin", "vitamin b2"), "riboflavin"),
    (("niacin", "niacinamide", "nicotinamide", "vitamin b3"), "niacin"),
    (("pantothenic", "pantothenate", "vitamin b5"), "pantothenic_acid"),
    (("pyridoxine", "pyridoxal", "vitamin b6"), "vitamin_b6"),
    (("biotin", "vitamin b7"), "biotin"),
    (("folic acid", "folate", "methylfolate", "vitamin b9"), "folate"),
    (("vitamin b12", "cobalamin"), "vitamin_b12"),
    (("vitamin d", "cholecalciferol", "ergocalciferol"), "vitamin_d"),
    (("vitamin e", "tocopher", "tocotrien"), "vitamin_e"),
    (("vitamin k", "menaquinone", "phytonadione", "mk 7", "mk7"), "vitamin_k"),
    (("vitamin c", "ascorbic", "ascorbate"), "vitamin_c"),
    # Nutraceuticals R&D price one by one. Ubiquinol is listed before
    # ubiquinone because the two differ in a gummy: 40% against 25%.
    (("ubiquinol",), "ubiquinol"),
    (("coq10", "coenzyme q", "ubiquinone"), "coq10"),
    (("alpha lipoic", "lipoic acid"), "alpha_lipoic_acid"),
    (("lycopene",), "lycopene"),
    (("melatonin",), "melatonin"),
    (("lutein", "zeaxanthin", "astaxanthin"), "lutein"),
    # Spore formers survive processing far better than the rest: 20% against
    # 70%. Bacillus is the spore-forming genus used in supplements, but it is
    # a substring of Lactobacillus, which is not one -- so the non-spore
    # genera are tested first.
    (("lactobacillus", "bifidobacter", "lactococcus", "saccharomyces"),
     "probiotic_non_spore"),
    (("bacillus",), "probiotic_spore"),
    (("probiotic", "cfu"), "probiotic"),
    (("bromelain", "papain", "protease", "amylase", "lipase", "cellulase", "enzyme"),
     "enzyme"),
    (("fish oil", "epa", "dha", "omega 3", "krill", "flax oil", "algal oil"), "omega_oil"),
    (("iodine", "iodide"), "iodine"),
    # R&D group zinc, iron and boron with the macro minerals, not with the
    # trace group, which is chromium, copper, manganese, molybdenum, selenium.
    (("calcium", "magnesium", "zinc", "boron"), "calcium_magnesium"),
    (("iron", "ferrous", "ferric"), "iron"),
    (("copper", "manganese", "selenium", "selenomethionine", "selenate", "selenite",
      "chromium", "molybdenum"), "trace_minerals"),
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
    gummy: bool = False,
) -> Classification:
    """Decide potency and overage for one ingredient line."""

    # -- potency: part code, then identity default, then 1.0 + flag ---
    # R&D record one row per claim basis, so a part can carry several
    # potencies. Where it does, no lookup can choose between them; the line
    # is costed on the default and the bases are named for a person to pick.
    claim_bases: tuple[str, ...] = ()
    for code in (input_part_code, matched_code):
        claims = reference.potency_claims_for_part(code)
        if len(claims) > 1:
            claim_bases = tuple(
                f"{claim.claim_description} = {claim.potency_factor:g}" for claim in claims
            )
            break

    potency = reference.potency_for_part(input_part_code)
    potency_source = f"Potency table (part {input_part_code})" if potency else ""
    if potency is None:
        potency = reference.potency_for_part(matched_code)
        if potency:
            potency_source = f"Potency table (part {matched_code})"
    if potency is None and claim_bases:
        # An identity default would silently answer the question R&D left
        # open, so it is not consulted when the claim basis is the problem.
        return _ambiguous_potency(
            name, reference, resolution, claim_bases, multi_ingredient, gummy
        )
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

    overage_pct, overage_source, class_source_defaulted = _overage_for(
        reference, overage_class, multi_ingredient, gummy, class_source_defaulted
    )
    if overage_pct is None:
        overage_class = "default"
        class_source_defaulted = True
        overage_pct, overage_source, _ = _overage_for(
            reference, "default", multi_ingredient, gummy, True
        )
        overage_pct = overage_pct if overage_pct is not None else 0.05

    return Classification(
        potency=potency,
        potency_source=potency_source,
        potency_defaulted=potency_defaulted,
        overage_class=overage_class,
        overage_pct=overage_pct,
        overage_defaulted=class_source_defaulted,
        overage_source=overage_source,
    )


def _overage_for(
    reference: ReferenceData,
    overage_class: str,
    multi_ingredient: bool,
    gummy: bool,
    class_defaulted: bool,
) -> tuple[float | None, str, bool]:
    """The overage for one class, saying which of R&D's four columns it came from.

    R&D price gummies separately, because depositing and curing cost far more
    potency than blending and encapsulating do. Where they leave the gummy
    column blank -- probiotics read "Strain Dependent" -- the caps-and-tablets
    figure is used and the substitution is stated, never passed off as theirs.
    """
    entry = reference.overage_class(overage_class)
    if entry is None:
        return None, "", class_defaulted

    if class_defaulted:
        basis = "Reference default - class could not be determined"
    else:
        basis = f"Overage class '{overage_class}'"
    if not entry.from_guideline:
        basis += " - not in R&D's guideline, working value applied"

    if gummy:
        value = entry.pct(multi_ingredient, gummy=True)
        if value is not None:
            return value, f"{basis}, gummy column", class_defaulted
        fallback = entry.pct(multi_ingredient, gummy=False)
        if fallback is None:
            return None, "", class_defaulted
        return (
            fallback,
            f"{basis} - R&D give no gummy figure for this class; the "
            "caps/tablets/powder column was used",
            class_defaulted,
        )

    value = entry.pct(multi_ingredient, gummy=False)
    if value is None:
        return None, "", class_defaulted
    return value, basis, class_defaulted


def _ambiguous_potency(
    name: str,
    reference: ReferenceData,
    resolution: Resolution | None,
    claim_bases: tuple[str, ...],
    multi_ingredient: bool,
    gummy: bool,
) -> Classification:
    """Cost the line on the default potency and name the bases R&D record."""
    overage_class = resolution.overage_class if resolution is not None else None
    class_defaulted = False
    if not overage_class or overage_class == "default":
        overage_class = classify_by_name(name) or "default"
    if overage_class == "default":
        class_defaulted = True

    overage_pct, overage_source, class_defaulted = _overage_for(
        reference, overage_class, multi_ingredient, gummy, class_defaulted
    )
    if overage_pct is None:
        overage_class, class_defaulted = "default", True
        overage_pct, overage_source, _ = _overage_for(
            reference, "default", multi_ingredient, gummy, True
        )
        overage_pct = overage_pct if overage_pct is not None else 0.05

    return Classification(
        potency=reference.rate("default_potency", 1.0),
        potency_source=(
            "Claim basis unresolved - R&D record several for this part: "
            + "; ".join(claim_bases)
        ),
        potency_defaulted=True,
        overage_class=overage_class,
        overage_pct=overage_pct,
        overage_defaulted=class_defaulted,
        overage_source=overage_source,
    )
