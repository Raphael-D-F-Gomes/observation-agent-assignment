"""Checks for occurrence counting and the dollar formula.

Scope: sent times and ladder rungs. No corpus and no model.
"""

from __future__ import annotations

import unittest

from consolidate.pricing import occurrence_facts, price, snap_fraction, snap_minutes
from consolidate.constants import HOUR_RATE, MONTH_DAYS


class PricingTests(unittest.TestCase):
    """Formula, month floor, undated exclusion, and the sensitivity band."""

    def test_three_messages_on_one_day_are_one_occurrence(self) -> None:
        """Several messages that share a UTC date count as one active day."""
        sent = [
            "2001-03-02T09:00:00Z",
            "2001-03-02T15:00:00Z",
            "2001-03-02T18:00:00Z",
        ]
        occurrences, months, span, days = occurrence_facts(sent)
        self.assertEqual(occurrences, 1)
        self.assertEqual(days, ["2001-03-02"])
        self.assertEqual(span, 0)
        self.assertEqual(months, 1.0)

    def test_short_span_uses_one_month_and_drops_undated(self) -> None:
        """A span under a month stays one month, and a missing time is ignored."""
        sent = [
            "2001-01-05T10:00:00Z",
            None,
            "2001-01-19T10:00:00Z",
            "2001-01-05T16:00:00Z",
        ]
        occurrences, months, span, days = occurrence_facts(sent)
        self.assertEqual(occurrences, 2)
        self.assertEqual(span, 14)
        self.assertEqual(months, 1.0)
        self.assertLess(span / MONTH_DAYS, 1)

    def test_money_matches_the_formula_and_sensitivity(self) -> None:
        """Hours and dollars follow the formula, and the band uses minute neighbors."""
        sent = ["2001-01-05T10:00:00Z", "2001-01-12T10:00:00Z", "2001-01-19T10:00:00Z", None]
        result = price(sent, 30, 0.5)
        self.assertEqual(result.occurrences, 3)
        self.assertEqual(result.months, 1.0)
        self.assertEqual(result.minutes, 30)
        self.assertEqual(result.fraction, 0.5)
        expected_hours = 3 / 1.0 * 30 / 60 * 0.5
        self.assertAlmostEqual(result.hours_per_month, expected_hours)
        self.assertAlmostEqual(result.dollars_per_month, expected_hours * HOUR_RATE)
        low = price(sent, 15, 0.5)
        high = price(sent, 45, 0.5)
        self.assertAlmostEqual(result.dollars_low, low.dollars_per_month)
        self.assertAlmostEqual(result.dollars_high, high.dollars_per_month)
        again = price(sent, 30, 0.5)
        self.assertEqual(result.dollars_per_month, again.dollars_per_month)

    def test_ladder_ties_use_the_lower_rung(self) -> None:
        """A value exactly between two rungs snaps down."""
        self.assertEqual(snap_minutes(7.5), 5)
        self.assertEqual(snap_fraction(0.375), 0.25)

    def test_top_minute_rung_has_no_higher_neighbor(self) -> None:
        """The 60-minute rung uses itself as the high sensitivity value."""
        result = price(["2001-04-01T00:00:00Z", "2001-05-15T00:00:00Z", "2001-06-20T00:00:00Z"], 60, 1)
        self.assertEqual(result.minutes, 60)
        self.assertAlmostEqual(result.hours_high, result.hours_per_month)
        self.assertGreater(result.months, 1)


if __name__ == "__main__":
    unittest.main()
