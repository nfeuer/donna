import healthwatch as hw

# Minimal shapes from GET /containers/json?all=1 and /containers/{id}/json.
LIST = [
    {"Id": "1", "Names": ["/donna-orchestrator"], "State": "running",
     "Status": "Up 2 hours (healthy)"},
    {"Id": "2", "Names": ["/caddy"], "State": "exited",
     "Status": "Exited (1) 3 minutes ago"},
    {"Id": "3", "Names": ["/immich-server"], "State": "running",
     "Status": "Up 2 hours (healthy)"},
    {"Id": "4", "Names": ["/donna-api"], "State": "running",
     "Status": "Up 2 hours (unhealthy)"},
]


def fake_fetch(path):
    assert path == "/containers/json?all=1"
    return LIST


def test_watch_set_matches_prefix_and_extras():
    names = hw.resolve_watch_set(LIST, prefix="donna-", extras=["caddy"])
    assert names == {"donna-orchestrator", "caddy", "donna-api"}


def test_poll_builds_status_map_for_watched_only():
    status = hw.poll(fake_fetch, prefix="donna-", extras=["caddy"])
    assert status == {
        "donna-orchestrator": hw.OK,
        "donna-api": hw.UNHEALTHY,
        "caddy": hw.DOWN,
    }
    assert "immich-server" not in status


def test_poll_marks_configured_extra_missing_when_absent():
    status = hw.poll(fake_fetch, prefix="donna-", extras=["caddy", "ghost"])
    assert status["ghost"] == hw.MISSING


def test_parse_health_from_status_string():
    assert hw._health_from_status("Up 2 hours (healthy)") == "healthy"
    assert hw._health_from_status("Up 2 hours (unhealthy)") == "unhealthy"
    assert hw._health_from_status("Up 2 hours (health: starting)") == "starting"
    assert hw._health_from_status("Up 2 hours") is None
