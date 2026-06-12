"""Tests for the TimeIntent value object and deadline derivation."""

from datetime import UTC, datetime

from donna.scheduling.time_intent import TimeIntent, derive_deadline, derive_deadline_type


def _dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_exact_round_trips_through_json():
    ti = TimeIntent(kind="exact", due_at=_dt(2026, 6, 6, 14), strictness="hard")
    restored = TimeIntent.from_json(ti.to_json())
    assert restored == ti


def test_none_kind_has_no_times():
    ti = TimeIntent.from_dict({"kind": "none"})
    assert ti.kind == "none"
    assert ti.due_at is None and ti.latest is None


def test_derive_deadline_prefers_due_at_then_latest():
    exact = TimeIntent(kind="exact", due_at=_dt(2026, 6, 6, 14), strictness="hard")
    window = TimeIntent(
        kind="window", earliest=_dt(2026, 6, 6), latest=_dt(2026, 6, 13), strictness="soft"
    )
    assert derive_deadline(exact) == _dt(2026, 6, 6, 14)
    assert derive_deadline(window) == _dt(2026, 6, 13)
    assert derive_deadline(TimeIntent(kind="none")) is None
    assert derive_deadline(TimeIntent(kind="recurring")) is None


def test_derive_deadline_type_maps_strictness_else_none():
    assert derive_deadline_type(TimeIntent(kind="exact", strictness="hard")) == "hard"
    assert derive_deadline_type(TimeIntent(kind="window", strictness="soft")) == "soft"
    assert derive_deadline_type(TimeIntent(kind="none")) == "none"
    assert derive_deadline_type(TimeIntent(kind="recurring")) == "none"
