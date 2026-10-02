"""The ListenBrainz service: the username cache must follow the token.

The cache exists so a request does not pay a ``validate-token`` call every time. It is
keyed by the token, not just held as a string, so logging out and in as another account
cannot serve the previous user's name (and library).
"""

from musicare_metadata_plugin_sdk import AuthContext

from src.credentials import Credentials
from src.listenbrainz import ListenBrainz

from ._stubs import StubClient


def _client(name):
    """A stub that answers ``validate-token`` with the current ``name``."""
    return StubClient(
        lambda url, params: {"user_name": name["value"]} if "validate-token" in url else {}
    )


def _validations(client):
    return sum(1 for kind, url, _ in client.calls if "validate-token" in url)


def _credentials(tmp_path, token):
    context = AuthContext(data_dir=str(tmp_path), plugin_id="test")
    credentials = Credentials()
    credentials.store(context, token)
    return credentials, context


def test_username_is_served_from_the_cache_while_the_token_is_unchanged(tmp_path):
    client = _client({"value": "alice"})
    credentials, _ = _credentials(tmp_path, "token-a")
    lb = ListenBrainz(client, credentials)

    assert lb.username() == "alice"
    assert lb.username() == "alice"

    assert _validations(client) == 1


def test_changing_the_token_invalidates_the_username_cache(tmp_path):
    client = _client({"value": "alice"})
    credentials, context = _credentials(tmp_path, "token-a")
    lb = ListenBrainz(client, credentials)
    assert lb.username() == "alice"

    # Same service instance, another account: the token changes under it.
    client.handler = lambda url, params: (
        {"user_name": "bob"} if "validate-token" in url else {}
    )
    credentials.store(context, "token-b")

    assert lb.username() == "bob"
    assert _validations(client) == 2
    assert lb.username() == "bob"
    assert _validations(client) == 2


def test_logout_drops_the_cached_username(tmp_path):
    client = _client({"value": "alice"})
    credentials, context = _credentials(tmp_path, "token-a")
    lb = ListenBrainz(client, credentials)
    assert lb.username() == "alice"

    credentials.clear(context)

    assert lb.username() == "listenbrainz"


def test_a_rejected_token_is_not_cached(tmp_path):
    client = StubClient(lambda url, params: {})
    credentials, _ = _credentials(tmp_path, "bad")
    lb = ListenBrainz(client, credentials)

    assert lb.username() == "listenbrainz"
    assert lb.username() == "listenbrainz"

    assert _validations(client) == 2
