import healthwatch as hw


def test_first_seen_bad_emits_bad():
    events = hw.diff({}, {"donna-api": hw.UNHEALTHY})
    assert events == [hw.Event("donna-api", "bad", hw.UNHEALTHY)]


def test_first_seen_ok_emits_nothing():
    assert hw.diff({}, {"donna-api": hw.OK}) == []


def test_ok_to_bad_emits_bad():
    events = hw.diff({"caddy": hw.OK}, {"caddy": hw.DOWN})
    assert events == [hw.Event("caddy", "bad", hw.DOWN)]


def test_bad_to_ok_emits_recovered():
    events = hw.diff({"caddy": hw.DOWN}, {"caddy": hw.OK})
    assert events == [hw.Event("caddy", "recovered", hw.OK)]


def test_unchanged_bad_emits_nothing():
    assert hw.diff({"caddy": hw.DOWN}, {"caddy": hw.DOWN}) == []


def test_bad_to_different_bad_emits_nothing():
    # Still bad; we already alerted. No second ping on UNHEALTHY->DOWN.
    assert hw.diff({"x": hw.UNHEALTHY}, {"x": hw.DOWN}) == []


def test_missing_is_bad():
    events = hw.diff({"donna-api": hw.OK}, {"donna-api": hw.MISSING})
    assert events == [hw.Event("donna-api", "bad", hw.MISSING)]


def test_events_sorted_by_name_for_determinism():
    cur = {"b": hw.DOWN, "a": hw.DOWN}
    names = [e.name for e in hw.diff({}, cur)]
    assert names == ["a", "b"]
