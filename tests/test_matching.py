"""Tier behaviour and the Accepted-only cost rule."""
from quickquote.config import ACCEPTED, NEEDS_REVIEW, UNMATCHED
from quickquote.engine.matching import match_line


class TestTiers:
    def test_tier1_exact_part_code_is_accepted(self, reference):
        result = match_line("anything at all", "RM-1042", reference)
        assert result.status == ACCEPTED
        assert result.method.startswith("Tier 1")
        assert result.matched_code == "RM-1042"

    def test_tier2_single_po_record_is_accepted(self, reference):
        result = match_line("Coenzyme Q10", None, reference)
        assert result.status == ACCEPTED
        assert result.method.startswith("Tier 2")

    def test_tier2_multiple_po_records_need_review(self, reference):
        result = match_line("Ashwagandha Extract", None, reference)
        assert result.status == NEEDS_REVIEW
        assert len(result.candidates) > 1

    def test_tier3_no_alias_is_unmatched(self, reference):
        result = match_line("Unobtainium Complex", None, reference)
        assert result.status == UNMATCHED

    def test_tier3_known_identity_without_po_record_is_unmatched(self, reference):
        result = match_line("Aged Garlic Extract", None, reference)
        assert result.status == UNMATCHED
        assert result.identity == "aged garlic extract"

    def test_unknown_part_code_falls_through_to_identity(self, reference):
        result = match_line("Coenzyme Q10", "NOT-A-PART", reference)
        assert result.status == ACCEPTED
        assert result.method.startswith("Tier 2")


class TestPackagingSize:
    def test_exact_size_matches(self, reference):
        assert match_line("HDPE Bottle White 175cc", None, reference,
                          packaging=True).matched_code == "PK-1175"
        assert match_line("HDPE Bottle White 250cc", None, reference,
                          packaging=True).matched_code == "PK-1250"

    def test_size_is_never_substituted(self, reference):
        result = match_line("HDPE Bottle White 999cc", None, reference, packaging=True)
        assert result.status == UNMATCHED
        assert "size is never substituted" in result.reason

    def test_missing_size_is_not_guessed(self, reference):
        result = match_line("White HDPE Bottle", None, reference, packaging=True)
        assert result.status == UNMATCHED

    def test_gelatin_and_veggie_shells_resolve_separately(self, reference):
        veggie = match_line("Vegetable Capsule Shell Size 00", None, reference, packaging=True)
        gelatin = match_line("Gelatin Capsule Shell Size 00", None, reference, packaging=True)
        assert veggie.matched_code == "PK-4000"
        assert gelatin.matched_code == "PK-4010"
        assert veggie.matched_code != gelatin.matched_code
