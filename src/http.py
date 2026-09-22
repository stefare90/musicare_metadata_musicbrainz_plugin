"""Standard-library HTTP client for the plugin's external providers.

The plugin must stay 100% Pure-Python (no native wheels), and the handful of providers it
talks to do not justify a dependency, so ``urllib`` is the transport. On top of it this
module adds the provider etiquette the contract requires: an identifying ``User-Agent``,
a per-host minimum interval (MusicBrainz allows roughly one request per second), respect
for ``Retry-After`` on 429/503, and the mapping from HTTP outcomes to the SDK error codes.
"""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional

from musicare_metadata_plugin_sdk import (
    AuthRequiredError,
    MetadataPluginError,
    NotFoundError,
    RateLimitedError,
    TransportError,
)

USER_AGENT = (
    "MusicAreMetadataMusicBrainz/1.0.0 "
    "( https://github.com/stefare90/musicare_metadata_musicbrainz_plugin )"
)

# MusicBrainz asks clients not to exceed one request per second.
RATE_LIMITS: Dict[str, float] = {"musicbrainz.org": 1.0}

_RETRYABLE_STATUS = (429, 503)
_RETRYABLE_ATTEMPTS = 3


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

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        interval = self._rate_limits.get(host)
        if not interval:
            return
        last = self._last_request.get(host)
        if last is not None:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: Optional[bytes],
        timeout: float,
    ):
        request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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
            if status in _RETRYABLE_STATUS:
                if attempt < _RETRYABLE_ATTEMPTS:
                    attempt += 1
                    delay = _retry_after(response_headers)
                    time.sleep(float(attempt) if delay is None else delay)
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

    def get_json_or_none(self, url: str, **kwargs: Any) -> Any:
        """Best-effort variant for optional enrichment: any failure degrades to ``None``."""
        try:
            return self.get_json(url, **kwargs)
        except MetadataPluginError:
            return None
