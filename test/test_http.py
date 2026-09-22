"""``HttpClient``: envelope handling, provider etiquette and error mapping (no network)."""

import email.message
import io
import json
import urllib.error

import pytest

from musicare_metadata_plugin_sdk import NotFoundError, RateLimitedError, TransportError

from src import http


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


def test_get_json_sends_identity_headers_and_encodes_params(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, **kwargs):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(200, json.dumps({"ok": True}))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    client = http.HttpClient(rate_limits={})

    result = client.get_json(
        "https://example.test/x", params={"q": "a b", "fmt": "json"}, timeout=3.0
    )

    assert result == {"ok": True}
    request = captured["request"]
    assert request.get_header("User-agent") == http.USER_AGENT
    assert request.get_header("Accept") == "application/json"
    assert request.full_url == "https://example.test/x?q=a%20b&fmt=json"
    assert captured["timeout"] == pytest.approx(3.0, abs=0.1)


def test_empty_body_is_none(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request, "urlopen", lambda request, timeout=None, **kwargs: FakeResponse(200, "  ")
    )

    assert http.HttpClient(rate_limits={}).post_json("https://example.test/x") is None


def test_invalid_json_is_a_transport_error(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request, "urlopen", lambda request, timeout=None, **kwargs: FakeResponse(200, "<html>")
    )

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_404_maps_to_not_found(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", _raise(_http_error(404)))

    with pytest.raises(NotFoundError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_network_failure_is_a_transport_error(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", _raise(urllib.error.URLError("dns")))

    with pytest.raises(TransportError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")


def test_optional_call_absorbs_every_failure(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", _raise(_http_error(500, body="boom")))

    assert http.HttpClient(rate_limits={}).get_json_or_none("https://example.test/x") is None


def test_rate_limited_is_retried_then_reported(monkeypatch):
    calls = {"count": 0}

    def fake_urlopen(request, timeout=None, **kwargs):
        calls["count"] += 1
        raise _http_error(429, headers={"Retry-After": "0"})

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)

    with pytest.raises(RateLimitedError):
        http.HttpClient(rate_limits={}).get_json("https://example.test/x")

    assert calls["count"] == http._RETRYABLE_ATTEMPTS + 1


def test_retry_after_zero_is_respected(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def fake_urlopen(request, timeout=None, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _http_error(503, headers={"Retry-After": "0"})
        return FakeResponse(200, json.dumps({"ok": True}))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
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
    monkeypatch.setattr(
        http.urllib.request, "urlopen", lambda request, timeout=None, **kwargs: FakeResponse(200, "{}")
    )
    client = http.HttpClient(rate_limits={"musicbrainz.org": 1.0})

    client.get_json("https://musicbrainz.org/ws/2/recording")
    clock["now"] += 0.25
    client.get_json("https://musicbrainz.org/ws/2/release")

    assert sleeps and sleeps[0] == pytest.approx(0.75)


def test_retry_after_parses_seconds_and_rejects_garbage():
    assert http._retry_after({"Retry-After": "5"}) == 5.0
    assert http._retry_after({"retry-after": "0"}) == 0.0
    assert http._retry_after({"Retry-After": "soon"}) is None
    assert http._retry_after({}) is None
