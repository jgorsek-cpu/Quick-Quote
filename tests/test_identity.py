"""The identity rules the specification states as hard requirements."""
from quickquote.engine.identity import guard_blocks, guard_conflict, resolve_identity
from quickquote.engine.text import alias_matches, extract_size, tight


def test_short_alias_does_not_match_inside_compact_text(reference):
    """The named collision: msm must not match 'mm smooth silver'."""
    assert "msm" in tight("MM Smooth Silver")     # the trap the rule guards
    assert alias_matches("MSM Powder OptiMSM", "msm")
    assert not alias_matches("MM Smooth Silver Powder", "msm")


def test_long_alias_tolerates_punctuation(reference):
    assert resolve_identity("Coenzyme Q-10 USP", reference).canonical == "coenzyme q10"
    assert resolve_identity("CoQ10 100mg", reference).canonical == "coenzyme q10"


def test_longest_alias_wins(reference):
    resolution = resolve_identity("Glucosamine Sulfate 2KCl", reference)
    assert resolution.canonical == "glucosamine sulfate"
    assert resolution.alias == "glucosamine sulfate 2kcl"


def test_unknown_text_has_no_identity(reference):
    assert resolve_identity("Unobtainium Complex", reference) is None


class TestGuardList:
    def test_garlic_never_matches_turmeric(self, reference):
        assert guard_blocks(reference, "aged garlic extract", "turmeric extract")
        assert guard_blocks(reference, "garlic powder", "turmeric powder")

    def test_red_yeast_rice_never_matches_red_clover(self, reference):
        assert guard_blocks(reference, "red yeast rice", "red clover extract")

    def test_glucosamine_forms_never_match(self, reference):
        assert guard_blocks(reference, "glucosamine sulfate", "glucosamine hcl")

    def test_gelatin_never_matches_veggie_capsules(self, reference):
        assert guard_blocks(reference, "gelatin capsule shell", "vegetable capsule shell")

    def test_identical_identity_is_never_blocked(self, reference):
        assert guard_blocks(reference, "coenzyme q10", "coenzyme q10") is None

    def test_description_spanning_a_guarded_pair_is_ambiguous(self, reference):
        resolution = resolve_identity("Turmeric Powder and Garlic Powder Blend", reference)
        assert guard_conflict(reference, resolution) is not None


class TestSize:
    def test_bottle_volume_must_match_exactly(self):
        assert extract_size("bottle 175cc").conflicts_with(extract_size("bottle 250cc"))

    def test_capsule_size_00_is_not_size_0(self):
        assert extract_size("capsule size 00").capsule_size == "00"
        assert extract_size("capsule size 0").capsule_size == "0"
        assert extract_size("capsule size 00").conflicts_with(extract_size("capsule size 0"))

    def test_unstated_dimension_is_not_a_conflict(self):
        stated = extract_size("bottle 175cc")
        full = extract_size("bottle 175cc 45mm")
        assert not stated.conflicts_with(full)
        assert full.covers(stated)
