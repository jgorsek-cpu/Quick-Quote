"""Per-line and quote-level confidence."""
from datetime import date, timedelta

from quickquote.config import ACCEPTED, HIGH, LOW, MEDIUM, NEEDS_REVIEW, UNMATCHED
from quickquote.engine.confidence import line_confidence, quote_confidence
from quickquote.schemas import CostedIngredient, MatchResult

from .conftest import AS_OF

FRESH = AS_OF - timedelta(days=30)     # under six months
AGEING = AS_OF - timedelta(days=250)   # six months to a year
STALE = AS_OF - timedelta(days=400)    # over a year


def match(status=ACCEPTED, method="Tier 1 - exact part code", po_date=FRESH):
    return MatchResult(status=status, reason="", method=method, latest_po_date=po_date)


class TestLineConfidence:
    def test_exact_match_on_a_fresh_po_is_high(self, reference):
        assert line_confidence(match(), reference, AS_OF) == HIGH

    def test_exact_match_on_an_ageing_po_drops_to_medium(self, reference):
        """Past six months the price is no longer strong evidence of today's cost."""
        assert line_confidence(match(po_date=AGEING), reference, AS_OF) == MEDIUM

    def test_a_price_over_a_year_old_is_low_however_it_matched(self, reference):
        assert line_confidence(match(po_date=STALE), reference, AS_OF) == LOW
        assert line_confidence(
            match(method="Tier 2 - identity match", po_date=STALE), reference, AS_OF) == LOW

    def test_a_price_with_no_date_is_low(self, reference):
        """An unknown-age price is not a confident one."""
        assert line_confidence(match(po_date=None), reference, AS_OF) == LOW

    def test_identity_match_is_medium(self, reference):
        assert line_confidence(match(method="Tier 2 - identity match"), reference, AS_OF) == MEDIUM

    def test_anything_not_accepted_is_low(self, reference):
        assert line_confidence(match(status=NEEDS_REVIEW), reference, AS_OF) == LOW
        assert line_confidence(match(status=UNMATCHED), reference, AS_OF) == LOW


def line(cost, confidence, status=ACCEPTED):
    costed = CostedIngredient(name="x", match=MatchResult(status=status, reason=""))
    costed.cost_per_bottle = cost
    costed.confidence = confidence
    return costed


class TestQuoteConfidence:
    def test_all_high_major_spend_is_high(self, reference):
        assert quote_confidence([line(5.0, HIGH), line(0.01, MEDIUM)], [], reference) == HIGH

    def test_a_medium_major_spend_line_makes_the_quote_medium(self, reference):
        assert quote_confidence([line(5.0, MEDIUM), line(1.0, HIGH)], [], reference) == MEDIUM

    def test_any_outstanding_line_makes_the_quote_low(self, reference):
        lines = [line(5.0, HIGH), line(None, LOW, status=UNMATCHED)]
        assert quote_confidence(lines, [], reference) == LOW

    def test_a_small_medium_line_does_not_drag_the_quote_down(self, reference):
        """Only lines above 10% of primary cost count as major spend."""
        assert quote_confidence([line(9.0, HIGH), line(0.05, MEDIUM)], [], reference) == HIGH
