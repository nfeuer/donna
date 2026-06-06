import healthwatch as hw


def rec(name, state, health=None):
    return {"name": name, "state": state, "health": health}


def test_running_healthy_is_ok():
    assert hw.classify(rec("donna-api", "running", "healthy")) == hw.OK


def test_running_no_healthcheck_is_ok():
    assert hw.classify(rec("caddy", "running", None)) == hw.OK


def test_running_starting_is_ok():
    # health:starting during boot must not false-alarm
    assert hw.classify(rec("donna-orchestrator", "running", "starting")) == hw.OK


def test_running_unhealthy():
    assert hw.classify(rec("donna-orchestrator", "running", "unhealthy")) == hw.UNHEALTHY


def test_exited_is_down():
    assert hw.classify(rec("caddy", "exited", None)) == hw.DOWN


def test_restarting_is_down():
    assert hw.classify(rec("donna-ui", "restarting", None)) == hw.DOWN


def test_paused_and_created_and_dead_are_down():
    for s in ("paused", "created", "dead"):
        assert hw.classify(rec("x", s, None)) == hw.DOWN
