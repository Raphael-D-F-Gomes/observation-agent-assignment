"""Priced result of one process. Published rows form the dollar total.

id: opportunity id.
process_id: process this price belongs to.
status: published, incident, or rejected.
occurrences: distinct UTC dates among the primary events.
months: span of those dates divided by 30.44, and at least 1.
minutes: snapped minutes per occurrence.
fraction: snapped automatable fraction.
hours_per_month: occurrences / months * minutes / 60 * fraction.
dollars_per_month: hours_per_month times the hourly rate.
hours_low: hours at the lower neighboring minute rung.
hours_high: hours at the higher neighboring minute rung.
dollars_low: hours_low times the hourly rate.
dollars_high: hours_high times the hourly rate.
stated_cadence: quote of a stated cadence. Null when none passed.
assumptions_json: snapped minutes, fraction, ladders, and rationale.
formula_json: days, people, rate, and the expression behind the price.
"""

SQL = """
CREATE TABLE IF NOT EXISTS opportunities (
  id                  TEXT PRIMARY KEY,
  process_id          TEXT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('published','incident','rejected')),
  occurrences         INTEGER NOT NULL,
  months              REAL NOT NULL,
  minutes             INTEGER NOT NULL,
  fraction            REAL NOT NULL,
  hours_per_month     REAL NOT NULL,
  dollars_per_month   REAL NOT NULL,
  hours_low           REAL NOT NULL,
  hours_high          REAL NOT NULL,
  dollars_low         REAL NOT NULL,
  dollars_high        REAL NOT NULL,
  stated_cadence      TEXT,
  assumptions_json    TEXT NOT NULL,
  formula_json        TEXT NOT NULL
);
"""
