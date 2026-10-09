"""Standard-library HTTP client for the plugin's external providers.

The plugin must stay 100% Pure-Python (no native wheels), and the handful of providers it
talks to do not justify a dependency, so ``urllib`` is the transport. On top of it this
module adds the provider etiquette the contract requires: an identifying ``User-Agent``,
a per-host minimum interval (MusicBrainz allows roughly one request per second), respect
for ``Retry-After`` on 429/503, and the mapping from HTTP outcomes to the SDK error codes.
"""

import http.client
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional

from musicare_metadata_plugin_sdk import (
    AuthRequiredError,
    NotFoundError,
    RateLimitedError,
    TransportError,
)

USER_AGENT = (
    "MusicAreMetadataMusicBrainz/1.0.0 "
    "( https://github.com/stefare90/musicare_metadata_musicbrainz_plugin )"
)


def user_agent_for(version: str) -> str:
    """The identifying agent ListenBrainz requires: app, version and a contact URL."""
    return (
        f"MusicAreMetadataMusicBrainz/{version} "
        "( https://github.com/stefare90/musicare_metadata_musicbrainz_plugin )"
    )

# One request per second per throttled host: MusicBrainz cuts off bursting IPs with
# 503, ListenBrainz answers 429 past one call per second per app, and the Cover Art
# Archive denies with 503. Server budget headers can only add waiting, never remove it.
RATE_LIMITS: Dict[str, float] = {
    "musicbrainz.org": 1.0,
    "api.listenbrainz.org": 1.0,
    "labs.api.listenbrainz.org": 1.0,
    "coverartarchive.org": 1.0,
}

_RETRYABLE_STATUS = (429, 503)
_RETRYABLE_ATTEMPTS = 3
_MAX_REDIRECTS = 5


def _ssl_context() -> ssl.SSLContext:
    """Build the TLS context, preferring certifi's CA bundle.

    The CPython embedded in the Android app ships no system CA store, so a plain
    ``urlopen`` fails with ``CERTIFICATE_VERIFY_FAILED``. ``certifi`` is pure-Python and
    is vendored into ``plugin.zip``; on a desktop the system store would work as well.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _retry_after(headers: Mapping[str, str]) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or HTTP-date)."""
    value = _header(headers, "Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def _reset_in(headers: Mapping[str, str]) -> Optional[float]:
    """Seconds until the rate-limit window resets (ListenBrainz `X-RateLimit-Reset-In`)."""
    value = _header(headers, "X-RateLimit-Reset-In")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _rate_limit_delay(headers: Mapping[str, str], attempt: int) -> float:
    """Backoff guided by the server: window reset, then `Retry-After`, then linear."""
    reset_in = _reset_in(headers)
    if reset_in is not None:
        return reset_in
    retry_after = _retry_after(headers)
    if retry_after is not None:
        return retry_after
    return float(attempt)


def _with_params(url: str, params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value is not None},
        quote_via=urllib.parse.quote,
    )
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"


def _parse_json(text: str, url: str) -> Any:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise TransportError(f"invalid JSON from {url}: {error}") from error


class HttpClient:
    """A small synchronous JSON client with retries and provider throttling."""

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        timeout: float = 15.0,
        rate_limits: Optional[Mapping[str, float]] = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._rate_limits = dict(rate_limits or RATE_LIMITS)
        self._last_request: Dict[str, float] = {}
        # Earliest monotonic time a host may be called again, from `X-RateLimit-*`.
        self._not_before: Dict[str, float] = {}
        self._ssl_context = _ssl_context()
        # One persistent stdlib connection per (scheme, host), kept thread-local:
        # the host serves concurrent calls on threads, and an ``http.client``
        # connection cannot interleave two requests. Threads never share a
        # connection, while each thread keeps reusing its own.
        self._local = threading.local()

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        now = time.monotonic()
        wait = 0.0
        interval = self._rate_limits.get(host)
        if interval:
            last = self._last_request.get(host)
            if last is not None:
                wait = max(wait, interval - (now - last))
        wait = max(wait, self._not_before.get(host, 0.0) - now)
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _observe_budget(self, url: str, headers: Mapping[str, str]) -> None:
        """Honor an exhausted server budget: an empty window defers the next call.

        Only `Remaining == 0` moves the gate, and only forward; a generous budget never
        shortens the configured interval (e.g. MusicBrainz' CDN counters sit far above
        the 1/s IP rule they must not override).
        """
        remaining = _header(headers, "X-RateLimit-Remaining")
        if remaining is None or remaining.strip() != "0":
            return
        reset_in = _reset_in(headers)
        if reset_in is None:
            return
        host = urllib.parse.urlsplit(url).netloc
        self._not_before[host] = max(
            self._not_before.get(host, 0.0), time.monotonic() + reset_in
        )

    def _pool(self) -> Dict[str, http.client.HTTPConnection]:
        pool = getattr(self._local, "pool", None)
        if pool is None:
            pool = self._local.pool = {}
        return pool

    def _connection_for(self, scheme: str, host: str, timeout: float) -> http.client.HTTPConnection:
        key = f"{scheme}://{host}"
        pool = self._pool()
        connection = pool.get(key)
        if connection is not None:
            connection.timeout = timeout
            return connection
        if scheme == "https":
            connection = http.client.HTTPSConnection(host, context=self._ssl_context, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(host, timeout=timeout)
        pool[key] = connection
        return connection

    def _drop_connection(self, scheme: str, host: str) -> None:
        connection = self._pool().pop(f"{scheme}://{host}", None)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _send_via_pool(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: Optional[bytes],
        timeout: float,
    ):
        target = url
        request_headers = dict(headers)
        for _ in range(_MAX_REDIRECTS + 1):
            parts = urllib.parse.urlsplit(target)
            connection = self._connection_for(parts.scheme, parts.netloc, timeout)
            path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
            try:
                connection.request(method, path, body=data, headers=request_headers)
                response = connection.getresponse()
                body = response.read()
                status = response.status
                response_headers = dict(response.getheaders())
                if response.will_close:
                    self._drop_connection(parts.scheme, parts.netloc)
            except (http.client.HTTPException, OSError, socket.timeout) as error:
                self._drop_connection(parts.scheme, parts.netloc)
                if data is not None or target != url:
                    raise TransportError(f"{method} {url} failed: {error}") from error
                connection = self._connection_for(parts.scheme, parts.netloc, timeout)
                try:
                    connection.request(method, path, body=data, headers=request_headers)
                    response = connection.getresponse()
                    body = response.read()
                    status = response.status
                    response_headers = dict(response.getheaders())
                    if response.will_close:
                        self._drop_connection(parts.scheme, parts.netloc)
                except (http.client.HTTPException, OSError, socket.timeout) as retry_error:
                    self._drop_connection(parts.scheme, parts.netloc)
                    raise TransportError(f"{method} {url} failed: {retry_error}") from retry_error
            if status not in (301, 302, 303, 307, 308):
                return status, response_headers, body.decode("utf-8", "replace")
            location = response_headers.get("Location") or response_headers.get("location")
            if not location:
                return status, response_headers, body.decode("utf-8", "replace")
            next_url = urllib.parse.urljoin(target, location)
            if urllib.parse.urlsplit(next_url).netloc != parts.netloc:
                request_headers.pop("Authorization", None)
            if status == 303 or (status in (301, 302) and method == "POST"):
                method, data = "GET", None
            target = next_url
        raise TransportError(f"{method} {url} redirected too many times")

    def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: Optional[bytes],
        timeout: float,
    ):
        if urllib.request.getproxies().get(urllib.parse.urlsplit(url).scheme):
            return self._send_via_urlopen(method, url, headers, data, timeout)
        return self._send_via_pool(method, url, headers, data, timeout)

    def _send_via_urlopen(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: Optional[bytes],
        timeout: float,
    ):
        request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=self._ssl_context
            ) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            return error.code, dict(error.headers or {}), body
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            raise TransportError(f"{method} {url} failed: {error}") from error

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Any = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Return the parsed JSON body, raising a typed SDK error on failure."""
        target = _with_params(url, params)
        request_headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        request_headers.update(headers or {})
        payload: Optional[bytes] = None
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            payload = json.dumps(body).encode("utf-8")

        budget = timeout or self._timeout
        deadline = time.monotonic() + budget
        attempt = 0
        while True:
            self._throttle(target)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(f"request budget exhausted for {target}")
            status, response_headers, text = self._send(
                method, target, request_headers, payload, min(budget, remaining)
            )
            self._observe_budget(target, response_headers)
            if status in _RETRYABLE_STATUS:
                if attempt < _RETRYABLE_ATTEMPTS:
                    attempt += 1
                    delay = _rate_limit_delay(response_headers, attempt)
                    if delay > deadline - time.monotonic():
                        raise RateLimitedError(
                            f"{target} throttled the request (HTTP {status})",
                            retryable=True,
                        )
                    time.sleep(delay)
                    continue
                raise RateLimitedError(
                    f"{target} throttled the request (HTTP {status})",
                    retryable=True,
                )
            if status == 404:
                raise NotFoundError(f"{target} returned 404")
            if status in (401, 403):
                # A rejected credential is the host's cue to open the login flow again.
                raise AuthRequiredError(f"{target} rejected the credentials (HTTP {status})")
            if not 200 <= status < 300:
                raise TransportError(f"HTTP {status} for {target}: {text[:200]}")
            return _parse_json(text, target)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)
