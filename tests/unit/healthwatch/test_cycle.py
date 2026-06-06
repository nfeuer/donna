import healthwatch as hw


def _ok_send(sent):
    def send(event):
        sent.append(event)
        return True
    return send


def test_process_cycle_successful_bad_advances_and_sends_once():
    sent = []
    new = hw.process_cycle({"A": hw.OK}, {"A": hw.UNHEALTHY}, send=_ok_send(sent))
    assert [e.kind for e in sent] == ["bad"]
    assert new["A"] == hw.UNHEALTHY


def test_process_cycle_failed_bad_retries_next_cycle():
    new = hw.process_cycle({}, {"A": hw.DOWN}, send=lambda e: False)
    # failed send must NOT mark the container as reported
    assert new.get("A") != hw.OK
    sent = []
    new2 = hw.process_cycle(new, {"A": hw.DOWN}, send=_ok_send(sent))
    assert [e.kind for e in sent] == ["bad"]  # re-fires
    assert new2["A"] == hw.DOWN


def test_process_cycle_failed_recovery_is_not_clobbered_and_retries():
    # A was bad and already alerted; now OK but the recovery post fails.
    new = hw.process_cycle({"A": hw.UNHEALTHY}, {"A": hw.OK}, send=lambda e: False)
    assert new["A"] == hw.UNHEALTHY  # retry marker preserved, NOT set to OK
    # next cycle the recovery send succeeds -> recovered re-fires, state advances
    sent = []
    new2 = hw.process_cycle(new, {"A": hw.OK}, send=_ok_send(sent))
    assert [e.kind for e in sent] == ["recovered"]
    assert new2["A"] == hw.OK


def test_process_cycle_steady_ok_records_baseline_without_sending():
    def must_not_send(event):
        raise AssertionError("should not send for steady-state OK")
    new = hw.process_cycle({}, {"A": hw.OK}, send=must_not_send)
    assert new["A"] == hw.OK


def test_process_cycle_successful_recovery_advances():
    sent = []
    new = hw.process_cycle({"A": hw.DOWN}, {"A": hw.OK}, send=_ok_send(sent))
    assert [e.kind for e in sent] == ["recovered"]
    assert new["A"] == hw.OK
