"""``HttpClient``: envelope handling, provider etiquette and error mapping (no network)."""

import email.message
import io
import json
import urllib.error

import pytest

from musicare_metadata_plugin_sdk import NotFoundError, RateLimitedError, TransportError

from src.net import http


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body.encode("utf-8")
        self.headers = email.message.Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(status, headers=None, body=""):
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        "https://example.test", status, "error", message, io.BytesIO(body.encode("utf-8"))
    )


def _raise(error):
    def fake_urlopen(request, timeout=None, **kwargs):
        raise error

    return fake_urlopen


class PoolResponse:
    def __init__(self, status, body, headers=None, will_close=False):
        self.status = status
        self._body = body.encode("utf-8")
        self._headers = dict(headers or {})
        self.will_close = will_close

    def read(self):
        return self._body

    def getheaders(self):
        return list(self._headers.items())


class PoolConnection:
    created = []

    def __init__(self, host, handler, **kwargs):
        self.host = host
        self.timeout = kwargs.get("timeout")
        self.requests = []
        self.closed = False
        self._handler = handler
        PoolConnection.created.append(self)

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, dict(headers or {})))
        self._pending = self._handler(method, path, headers or {}, body)

    def getresponse(self):
        pending, self._pending = self._pending, None
        if isinstance(pending, BaseException):
            raise pending
        return pending

    def close(self):
        self.closed = True


def install_pool(monkeypatch, handler):
    """Route the stdlib connection pool through a scripted handler.

    ``handler(method, path, headers, body)`` returns a ``PoolResponse`` or raises.
    Returns the list of created connections, in order.
    """
    PoolConnection.created.clear()

    def factory(host, **kwargs):
        return PoolConnection(host, handler, **kwargs)

    monkeypatch.setattr(http.http.client, "HTTPSConnection", factory)
    monkeypatch.setattr(http.http.client, "HTTPConnection", factory)
    return PoolConnection.created


def test_get_json_sends_identity_headers_and_encodes_params(monkeypatch):
    captured = {}

    def handler(method, path, headers, body):
        captured["path"] = path
        captured["headers"] = dict(headers)
        captured["timeout"] = PoolConnection.created[-1].timeout
        return PoolResponse(200, json.dumps({"ok": True}))

    install_pool(monkeypatch, handler)
    client = http.HttpClient(rate_limits={})

    result = client.get_json(
        "https://example.test/x", params={"q": "a b", "fmt": "json"}, timeout=3.0
    )

    assert result == {"ok": True}
    assert captured["headers"]["User-Agent"] == http.USER_AGENT
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["path"] == "/x?q=a%20b&fmt=json"
    assert captured["timeout"] == pytest.approx(3.0, abs=0.1)


def test_empty_body_is_none(monkeypatch):
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "  "))

    assert http.HttpClient(rate_limits={}).post_json("https://example.test/x") is None


def test_invalid_json_is_a_transport_error(monkeypatch):
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "<html>"))

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_404_maps_to_not_found(monkeypatch):
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(404, ""))

    with pytest.raises(NotFoundError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_network_failure_is_a_transport_error(monkeypatch):
    def handler(method, path, headers, body):
        raise OSError("dns")

    install_pool(monkeypatch, handler)

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_rate_limited_is_retried_then_reported(monkeypatch):
    calls = {"count": 0}

    def handler(method, path, headers, body):
        calls["count"] += 1
        return PoolResponse(429, "", {"Retry-After": "0"})

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    with pytest.raises(RateLimitedError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")

    assert calls["count"] == http._RETRYABLE_ATTEMPTS + 1


def test_retry_after_zero_is_respected(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def handler(method, path, headers, body):
        calls["count"] += 1
        if calls["count"] == 1:
            return PoolResponse(503, "", {"Retry-After": "0"})
        return PoolResponse(200, json.dumps({"ok": True}))

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert http.HttpClient(rate_limits={}).get_json("https://example.test/x") == {"ok": True}
    assert calls["count"] == 2
    assert sleeps == [0.0]


def test_musicbrainz_minimum_interval_is_enforced(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", fake_sleep)
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}"))
    client = http.HttpClient(rate_limits={"musicbrainz.org": 1.0})

    client.get_json("https://musicbrainz.org/ws/2/recording")
    clock["now"] += 0.25
    client.get_json("https://musicbrainz.org/ws/2/release")

    assert sleeps and sleeps[0] == pytest.approx(0.75)


def test_user_agent_carries_version_and_contact():
    agent = http.user_agent_for("2.0.3")

    assert agent.startswith("MusicAreMetadataMusicBrainz/2.0.3 ")
    assert "github.com/stefare90/musicare_metadata_musicbrainz_plugin" in agent


def test_retry_after_parses_seconds_and_rejects_garbage():
    assert http._retry_after({"Retry-After": "5"}) == 5.0
    assert http._retry_after({"retry-after": "0"}) == 0.0
    assert http._retry_after({"Retry-After": "soon"}) is None
    assert http._retry_after({}) is None


def test_reset_in_parses_seconds_and_rejects_garbage():
    assert http._reset_in({"X-RateLimit-Reset-In": "2"}) == 2.0
    assert http._reset_in({"x-ratelimit-reset-in": "0"}) == 0.0
    assert http._reset_in({"X-RateLimit-Reset-In": "soon"}) is None
    assert http._reset_in({}) is None


def test_listenbrainz_minimum_interval_is_enforced(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", fake_sleep)
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}"))
    client = http.HttpClient()

    client.get_json("https://api.listenbrainz.org/1/stats/user/x/artists")
    clock["now"] += 0.25
    client.get_json("https://labs.api.listenbrainz.org/similar-artists/json")

    assert not sleeps, "different ListenBrainz hosts pace independently"

    client.get_json("https://api.listenbrainz.org/1/stats/user/x/artists")
    assert sleeps and sleeps[-1] == pytest.approx(0.75)


def test_cover_art_archive_minimum_interval_is_enforced(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", fake_sleep)
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}"))
    client = http.HttpClient()

    client.get_json("https://coverartarchive.org/release/aaa/front-250.jpg")
    clock["now"] += 0.25
    client.get_json("https://coverartarchive.org/release/bbb/front-250.jpg")

    assert sleeps and sleeps[0] == pytest.approx(0.75)


def test_reset_in_drives_the_retry_delay(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def handler(method, path, headers, body):
        calls["count"] += 1
        if calls["count"] == 1:
            return PoolResponse(429, "", {"X-RateLimit-Reset-In": "7"})
        return PoolResponse(200, json.dumps({"ok": True}))

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert http.HttpClient(rate_limits={}).get_json("https://example.test/x") == {"ok": True}
    assert sleeps == [7.0]


def test_reset_in_wins_over_retry_after(monkeypatch):
    sleeps = []

    def handler(method, path, headers, body):
        return PoolResponse(429, "", {"X-RateLimit-Reset-In": "2", "Retry-After": "60"})

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(RateLimitedError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")

    assert sleeps and all(delay <= 2.0 for delay in sleeps)


def test_empty_budget_defers_the_next_call(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))
    install_pool(
        monkeypatch,
        lambda method, path, headers, body: PoolResponse(
            200, "{}", headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "5"}
        ),
    )
    client = http.HttpClient(rate_limits={})

    client.get_json("https://example.test/first")
    client.get_json("https://example.test/second")

    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(5.0, abs=0.05)


def test_generous_budget_does_not_shorten_the_throttle(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", fake_sleep)
    install_pool(
        monkeypatch,
        lambda method, path, headers, body: PoolResponse(
            200, "{}", headers={"X-RateLimit-Remaining": "29"}
        ),
    )
    client = http.HttpClient(rate_limits={"musicbrainz.org": 1.0})

    client.get_json("https://musicbrainz.org/ws/2/recording")
    clock["now"] += 0.25
    client.get_json("https://musicbrainz.org/ws/2/release")

    assert sleeps == [pytest.approx(0.75)]


def test_unaffordable_server_delay_is_a_rate_limited_error(monkeypatch):
    sleeps = []

    def handler(method, path, headers, body):
        return PoolResponse(429, "", {"Retry-After": "9999"})

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(RateLimitedError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")

    assert sleeps == []


def test_connections_are_reused_per_host(monkeypatch):
    created = install_pool(
        monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}")
    )
    client = http.HttpClient(rate_limits={})

    client.get_json("https://example.test/a")
    client.get_json("https://example.test/b")
    client.get_json("https://other.test/c")

    assert [connection.host for connection in created] == ["example.test", "other.test"]
    assert [(method, path) for method, path, _ in created[0].requests] == [
        ("GET", "/a"),
        ("GET", "/b"),
    ]


def test_stale_connection_is_retried_once(monkeypatch):
    calls = {"count": 0}

    def handler(method, path, headers, body):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionResetError("server went away")
        return PoolResponse(200, json.dumps({"ok": True}))

    created = install_pool(monkeypatch, handler)

    assert http.HttpClient(rate_limits={}).get_json("https://example.test/x") == {"ok": True}
    assert len(created) == 2
    assert created[0].closed


def test_post_is_not_retried_on_connection_failure(monkeypatch):
    def handler(method, path, headers, body):
        raise ConnectionResetError("server went away")

    created = install_pool(monkeypatch, handler)

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).post_json("https://example.test/x", body={})

    assert len(created) == 1


def test_redirect_is_followed(monkeypatch):
    def handler(method, path, headers, body):
        if path == "/first":
            return PoolResponse(301, "", {"Location": "/other"})
        return PoolResponse(200, json.dumps({"ok": True}))

    created = install_pool(monkeypatch, handler)

    assert http.HttpClient(rate_limits={}).get_json("https://example.test/first") == {"ok": True}
    assert [request[1] for request in created[0].requests] == ["/first", "/other"]


def test_authorization_is_stripped_on_cross_host_redirect(monkeypatch):
    seen = []

    def handler(method, path, headers, body):
        seen.append(dict(headers))
        if not path == "/landed":
            return PoolResponse(301, "", {"Location": "https://other.test/landed"})
        return PoolResponse(200, json.dumps({"ok": True}))

    install_pool(monkeypatch, handler)
    client = http.HttpClient(rate_limits={})

    result = client.get_json(
        "https://example.test/start", headers={"Authorization": "Token secret"}
    )

    assert result == {"ok": True}
    assert seen[0]["Authorization"] == "Token secret"
    assert "Authorization" not in seen[1]


def test_connection_close_is_not_reused(monkeypatch):
    created = install_pool(
        monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}", will_close=True)
    )
    client = http.HttpClient(rate_limits={})

    client.get_json("https://example.test/a")
    client.get_json("https://example.test/b")

    assert len(created) == 2
    assert created[0].closed


def test_default_budget_is_fifteen_seconds():
    assert http.HttpClient(rate_limits={})._timeout == 15.0


def test_permanent_error_is_neither_retried_nor_slept_on(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def handler(method, path, headers, body):
        calls["count"] += 1
        return PoolResponse(500, "boom")

    install_pool(monkeypatch, handler)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")

    assert calls["count"] == 1
    assert sleeps == []


def test_throttle_wait_can_exhaust_a_tiny_budget(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(http.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(http.time, "sleep", fake_sleep)
    install_pool(monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}"))
    client = http.HttpClient(rate_limits={"musicbrainz.org": 1.0})

    client.get_json("https://musicbrainz.org/ws/2/first", timeout=10.0)
    clock["now"] += 0.5
    with pytest.raises(TransportError, match="budget exhausted"):
        client.get_json("https://musicbrainz.org/ws/2/second", timeout=0.1)


def test_concurrent_threads_never_share_a_connection(monkeypatch):
    import threading

    violations = []
    lock = threading.Lock()

    class TrackingConn(PoolConnection):
        def request(self, method, path, body=None, headers=None):
            with lock:
                if getattr(self, "_in_use", False):
                    violations.append(self.host)
                self._in_use = True
            try:
                super().request(method, path, body=body, headers=headers)
            finally:
                with lock:
                    self._in_use = False

    PoolConnection.created.clear()

    def slow_ok(method, path, headers, body):
        http.time.sleep(0.01)
        return PoolResponse(200, "{}")

    def factory(host, **kwargs):
        return TrackingConn(host, slow_ok, **kwargs)

    monkeypatch.setattr(http.http.client, "HTTPSConnection", factory)
    monkeypatch.setattr(http.http.client, "HTTPConnection", factory)
    client = http.HttpClient(rate_limits={})
    errors = []

    def work():
        try:
            for _ in range(5):
                client.get_json("https://example.test/x")
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert violations == []
    assert len(PoolConnection.created) == 4


def test_proxy_falls_back_to_urlopen(monkeypatch):
    created = install_pool(
        monkeypatch, lambda method, path, headers, body: PoolResponse(200, "{}")
    )
    monkeypatch.setattr(
        http.urllib.request, "getproxies", lambda: {"https": "http://proxy:8080"}
    )
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda request, timeout=None, **kwargs: FakeResponse(200, json.dumps({"ok": True})),
    )

    assert http.HttpClient(rate_limits={}).get_json("https://example.test/x") == {"ok": True}
    assert created == []
