import healthwatch as hw


class _FakePoster:
    def __init__(self, status=204):
        self.calls = []
        self._status = status

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self._status, ""


def test_format_message_bad_unhealthy():
    msg = hw.format_message(hw.Event("donna-orchestrator", "bad", hw.UNHEALTHY), host="box")
    assert "donna-orchestrator" in msg
    assert "UNHEALTHY" in msg
    assert msg.startswith("🔴")


def test_format_message_recovered():
    msg = hw.format_message(hw.Event("caddy", "recovered", hw.OK), host="box")
    assert msg.startswith("🟢")
    assert "recovered" in msg.lower()


def test_notify_posts_to_channel_with_bot_auth():
    poster = _FakePoster(status=204)
    ok = hw.notify(
        hw.Event("caddy", "bad", hw.DOWN),
        channel_id="123",
        token="secrettoken",
        host="box",
        poster=poster,
    )
    assert ok is True
    url, headers, body = poster.calls[0]
    assert url == "https://discord.com/api/v10/channels/123/messages"
    assert headers["Authorization"] == "Bot secrettoken"
    # Discord's Cloudflare front 403s the default urllib UA; a real UA is required.
    assert headers.get("User-Agent")
    assert "caddy" in body


def test_notify_returns_false_on_http_error():
    poster = _FakePoster(status=500)
    assert hw.notify(
        hw.Event("caddy", "bad", hw.DOWN),
        channel_id="123", token="t", host="box", poster=poster,
    ) is False


def test_notify_survives_non_dict_429_body():
    # A 429 whose body is JSON null must not crash; it retries and (here) fails.
    class _Poster429:
        def __init__(self):
            self.n = 0
        def __call__(self, url, headers, body):
            self.n += 1
            return 429, "null"  # json.loads("null") -> None
    poster = _Poster429()
    result = hw.notify(
        hw.Event("caddy", "bad", hw.DOWN),
        channel_id="123", token="t", host="box", poster=poster,
    )
    assert poster.n == 2  # original + one retry
    assert result is False
